/* eslint-disable react/only-export-components -- provider and its hook form one public socket API */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { API_BASE_URL } from "../services/api";
import api from "../services/api";
import BrowserNotificationPrompt from "../components/notifications/BrowserNotificationPrompt";
import { showMessageNotification, syncPushSubscription } from "../services/browserNotifications";

const configuredSocketUrl = (import.meta.env.VITE_SOCKET_URL || new URL(API_BASE_URL, window.location.origin).origin).trim();
// Socket.IO needs the server origin, not the REST /api/v1 prefix or a custom
// namespace accidentally copied from VITE_API_BASE_URL.
const SOCKET_URL = new URL(configuredSocketUrl, window.location.origin).origin;
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 10000];

const SocketContext = createContext(null);

export function SocketProvider({ children }) {
  const { user } = useAuth();
  const location = useLocation();
  const userId = user?.id || null;
  const socketRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const deniedAttemptRef = useRef(0);
  const [socket, setSocket] = useState(null);
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const [connectionState, setConnectionState] = useState("disconnected");
  const [notifications, setNotifications] = useState([]);
  const [unreadCounts, setUnreadCounts] = useState({});
  const seenMessageIdsRef = useRef(new Set());
  const locationRef = useRef(location.pathname);
  const originalTitleRef = useRef(document.title || "EditZone");

  useEffect(() => { locationRef.current = location.pathname; }, [location.pathname]);

  const totalUnreadMessages = Object.values(unreadCounts).reduce((sum, value) => sum + Number(value || 0), 0);
  const clearUnread = useCallback((requestId) => {
    if (!requestId) return;
    setUnreadCounts((current) => ({ ...current, [requestId]: 0 }));
  }, []);

  useEffect(() => {
    if (!userId) { setUnreadCounts({}); return undefined; }
    let active = true;
    api.get("/chat").then((response) => {
      if (!active) return;
      setUnreadCounts(Object.fromEntries((response.data?.conversations || []).map((item) => [item.request_id, item.unread_count || 0])));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [userId]);

  useEffect(() => {
    if (userId && "Notification" in window && Notification.permission === "granted") {
      syncPushSubscription().catch(() => undefined);
    }
  }, [userId]);

  useEffect(() => {
    document.title = totalUnreadMessages > 0 && document.visibilityState === "hidden"
      ? `(${totalUnreadMessages}) EditZone`
      : originalTitleRef.current;
    const restore = () => { if (document.visibilityState === "visible") document.title = originalTitleRef.current; };
    document.addEventListener("visibilitychange", restore);
    return () => document.removeEventListener("visibilitychange", restore);
  }, [totalUnreadMessages]);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
  }, []);

  const retryConnection = useCallback(() => {
    clearReconnectTimer();
    setConnectionError("");
    setReconnecting(true);
    setConnectionState("reconnecting");
    if (socketRef.current && !socketRef.current.connected) socketRef.current.connect();
  }, [clearReconnectTimer]);

  useEffect(() => {
    let cancelled = false;
    if (!userId) {
      clearReconnectTimer();
      socketRef.current?.disconnect();
      socketRef.current = null;
      setSocket(null);
      setConnected(false);
      setConnectionError("");
      setConnectionState("disconnected");
      return;
    }

    import("socket.io-client").then(({ io }) => {
      if (cancelled) return;

      const nextSocket = io(SOCKET_URL, {
        path: "/socket.io",
        withCredentials: true,
        auth: async (callback) => {
          try {
            const response = await api.post("/auth/socket-token");
            callback({ token: response.data.token });
          } catch (error) {
            callback({ token: null, auth_error: error?.response?.data?.message || "Unable to refresh chat authentication" });
          }
        },
        transports: ["websocket", "polling"],
        tryAllTransports: true,
        reconnection: true,
        reconnectionAttempts: Infinity,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 10000,
        randomizationFactor: 0.5,
      });

      socketRef.current = nextSocket;
      setSocket(nextSocket);
      nextSocket.on("connect", () => {
        clearReconnectTimer();
        deniedAttemptRef.current = 0;
        setConnected(true);
        setReconnecting(false);
        setConnectionError("");
        setConnectionState("connected");
      });
      nextSocket.on("disconnect", (reason) => {
        setConnected(false);
        const intentional = reason === "io client disconnect";
        setReconnecting(!intentional);
        setConnectionState(intentional ? "disconnected" : "reconnecting");
      });
      nextSocket.io.on("reconnect_attempt", () => {
        setReconnecting(true);
        setConnectionState("reconnecting");
      });
      nextSocket.on("connect_error", (error) => {
        setConnected(false);
        const message = String(error?.message || "");
        const terminalAuthError = /authentication token required|invalid or expired token|invalid token payload|session was revoked|account is unavailable/i.test(message);
        if (terminalAuthError) {
          clearReconnectTimer();
          setReconnecting(false);
          setConnectionState("authentication_error");
          setConnectionError("Your chat session expired. Please sign in again.");
          return;
        }
        // Transport/proxy/server restarts are recoverable. Socket.IO handles
        // active transport failures; middleware/handshake failures need a
        // bounded-delay manual reconnect because socket.active is false.
        setConnectionError("");
        setReconnecting(true);
        setConnectionState("reconnecting");
        if (!nextSocket.active && !reconnectTimerRef.current) {
          const index = Math.min(deniedAttemptRef.current, RECONNECT_DELAYS_MS.length - 1);
          deniedAttemptRef.current += 1;
          reconnectTimerRef.current = window.setTimeout(() => {
            reconnectTimerRef.current = null;
            if (!cancelled && !nextSocket.connected) nextSocket.connect();
          }, RECONNECT_DELAYS_MS[index]);
        }
      });
      nextSocket.on("notification", (data) => {
        setNotifications((prev) => [{ ...data, id: Date.now() }, ...prev].slice(0, 30));
      });
      nextSocket.on("message_notification", (data) => {
        const messageId = String(data?.id || "");
        const requestId = String(data?.request_id || data?.project_id || "");
        if (!messageId || !requestId || String(data?.sender_id) === String(userId) || seenMessageIdsRef.current.has(messageId)) return;
        seenMessageIdsRef.current.add(messageId);
        if (seenMessageIdsRef.current.size > 500) seenMessageIdsRef.current.delete(seenMessageIdsRef.current.values().next().value);
        const escapedRequestId = requestId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const sameOpenChat = document.visibilityState === "visible"
          && new RegExp(`^/(?:editor/)?chat/${escapedRequestId}/?$`).test(locationRef.current);
        if (sameOpenChat) return;
        setUnreadCounts((current) => ({ ...current, [requestId]: Number(current[requestId] || 0) + 1 }));
        const chatPath = user?.role === "editor" ? `/editor/chat/${requestId}` : `/chat/${requestId}`;
        showMessageNotification(data, chatPath).catch(() => undefined);
      });
      nextSocket.on("messages_read", (data) => {
        if (String(data?.reader_id) === String(userId)) clearUnread(data.request_id);
      });
    }).catch(() => {
      if (!cancelled) {
        setConnectionState("unavailable");
        setConnectionError("Chat networking could not be loaded. Refresh the application.");
      }
    });

    return () => {
      cancelled = true;
      clearReconnectTimer();
      socketRef.current?.disconnect();
      socketRef.current = null;
      setSocket(null);
    };
  }, [clearReconnectTimer, clearUnread, user?.role, userId]);

  return (
    <SocketContext.Provider value={{ socket, connected, reconnecting, connectionError, connectionState, retryConnection, notifications, setNotifications, unreadCounts, totalUnreadMessages, clearUnread }}>
      {children}
      {userId && <BrowserNotificationPrompt userId={userId} />}
    </SocketContext.Provider>
  );
}

export const useSocket = () => useContext(SocketContext);
