import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, CheckCheck, CircleCheck, CreditCard, Download, ExternalLink, Eye, FileText, Image, Link2, MessageSquare, Mic, Paperclip, Pause, Play, Send, ShieldCheck, Square, Trash2, UploadCloud, WalletCards } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useSocket } from "../../context/SocketContext";
import { Loader } from "../../components/common/UI";
import api from "../../services/api";
import { retryUploadScan, secureUpload, waitForUploadScan } from "../../services/media";
import { protectedMediaUrl } from "../../services/media";
import { containsPhoneNumber, PHONE_BLOCK_MESSAGE } from "../../utils/chatModeration";
import { FILE_LIMIT_MB, MAX_TEXT_MESSAGE_LENGTH, fileCategory, validateFileSize } from "../../config/uploadLimits";

const formatTime = (value) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit" }).format(date);
};

const toDisplayText = (value, fallback = "") => {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const messages = value
      .map((item) => toDisplayText(item?.msg ?? item?.message ?? item))
      .filter(Boolean);
    return messages.join(". ") || fallback;
  }
  if (value && typeof value === "object") {
    return toDisplayText(value.msg ?? value.message ?? value.detail, fallback);
  }
  return fallback;
};

const apiErrorMessage = (error, fallback) => toDisplayText(
  error?.response?.data?.detail
    ?? error?.response?.data?.message
    ?? error?.message,
  fallback,
);

const normalizeMessages = (value) => Array.isArray(value)
  ? value.filter((message) => message && typeof message === "object")
  : [];
const messageIdentity = (message) => message.client_message_id || message.id || message._id;
const mergeMessages = (current, incoming) => {
  const merged = new Map();
  [...normalizeMessages(current), ...normalizeMessages(incoming)].forEach((message) => {
    const key = messageIdentity(message);
    if (key) merged.set(String(key), { ...(merged.get(String(key)) || {}), ...message });
  });
  return [...merged.values()].sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
};
const newClientMessageId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

const VOICE_MAX_SECONDS = 10 * 60;
const FINAL_OUTPUT_MAX_BYTES = 1_000_000_000;
const VOICE_AUDIO_BITS_PER_SECOND = 128_000;
const VOICE_CONSTRAINTS = {
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
};
const formatDuration = (seconds) => {
  const safe = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, "0")}`;
};
function VoicePlayer({ source, message, downloadsAllowed, onError }) {
  const audioRef = useRef(null);
  const savedDuration = Math.max(1, Number(message.duration_seconds) || 1);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(savedDuration);
  const syncDuration = () => {
    const mediaDuration = audioRef.current?.duration;
    setDuration(Number.isFinite(mediaDuration) && mediaDuration > 0 ? mediaDuration : savedDuration);
  };
  const toggle = async () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) await audio.play(); else audio.pause();
  };
  return <div className="flex items-center gap-2 rounded-full bg-white/5 px-2 py-1.5">
    <audio ref={audioRef} src={source} preload="metadata" controlsList={downloadsAllowed ? undefined : "nodownload"} onLoadedMetadata={syncDuration} onDurationChange={syncDuration} onTimeUpdate={() => setPosition(audioRef.current?.currentTime || 0)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => { setPlaying(false); setPosition(0); }} onError={onError} />
    <button type="button" onClick={toggle} className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-gold text-black" aria-label={playing ? "Pause voice message" : "Play voice message"}>{playing ? <Pause size={15} /> : <Play size={15} />}</button>
    <input aria-label="Voice message position" type="range" min="0" max={Math.max(1, duration)} step="0.1" value={Math.min(position, duration)} onChange={(event) => { const value = Number(event.target.value); audioRef.current.currentTime = value; setPosition(value); }} className="min-w-0 flex-1 accent-amber-400" />
    <span className="shrink-0 text-[11px] tabular-nums text-slate-300">{formatDuration(position)} / {formatDuration(duration)}</span>
  </div>;
}
function Attachment({ message, watermark, downloadsAllowed, senderOwned }) {
  const [source, setSource] = useState("");
  const [mediaError, setMediaError] = useState("");
  const [openingViewOnce, setOpeningViewOnce] = useState(false);
  const isConsumedViewOnce = message.view_once && message.viewed_at;
  useEffect(() => {
    let active = true;
    if (message.view_once) return () => { active = false; };
    protectedMediaUrl(message.file_url).then((url) => active && setSource(url)).catch((error) => active && setMediaError(apiErrorMessage(error, "Preview unavailable")));
    return () => { active = false; };
  }, [message.file_url, message.view_once]);
  const openViewOnce = async () => {
    if (openingViewOnce || message.viewed_at) return;
    setOpeningViewOnce(true);
    setMediaError("");
    try {
      const response = await api.post(`/chat/${message.request_id}/messages/${message.id || message._id}/view-once`);
      const url = await protectedMediaUrl(response.data.file_url);
      setSource(url);
    } catch (error) {
      setMediaError(apiErrorMessage(error, "This media has already been viewed."));
    } finally {
      setOpeningViewOnce(false);
    }
  };
  const retryPreview = () => {
    setMediaError("");
    setSource("");
    protectedMediaUrl(message.file_url).then((url) => setSource(`${url}${url.includes("?") ? "&" : "?"}retry=${Date.now()}`)).catch((error) => setMediaError(apiErrorMessage(error, "Preview unavailable")));
  };
  const download = async () => {
    try {
      const url = await protectedMediaUrl(message.file_url, "download");
      window.location.assign(url);
    } catch (error) {
      setMediaError(apiErrorMessage(error, "Download is not permitted"));
    }
  };
  if (mediaError) return <div className="mb-2 rounded-lg bg-red-950/40 p-2 text-xs text-red-200">{mediaError}{!message.view_once && <button type="button" onClick={retryPreview} className="ml-2 underline">Retry preview</button>}</div>;
  if (message.view_once && senderOwned) return <div className="mb-2 rounded-lg bg-white/5 p-3 text-xs text-slate-300"><span className="mr-2 inline-grid h-5 w-5 place-items-center rounded-full border border-brand-gold">1</span>View Once Media · {message.viewed_at ? "Opened" : "Sent"}</div>;
  if (isConsumedViewOnce && !source) return <div className="mb-2 rounded-lg bg-white/5 p-3 text-xs text-slate-400"><span className="mr-2 inline-grid h-5 w-5 place-items-center rounded-full border border-slate-500">1</span>Opened · View Once Media already viewed</div>;
  if (message.view_once && !source) return <button type="button" onClick={openViewOnce} disabled={openingViewOnce} className="mb-2 flex w-full items-center justify-center gap-2 rounded-xl border border-brand-gold/20 bg-brand-gold/10 p-4 text-sm text-brand-gold disabled:opacity-60"><span className="inline-grid h-5 w-5 place-items-center rounded-full border border-brand-gold">1</span><Eye size={18} /> {openingViewOnce ? "Opening…" : "Open View Once Media"}</button>;
  if (!source) return <div className="mb-2 h-14 animate-pulse rounded-lg bg-white/10" />;
  const controls = <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-brand-goldLight/70"><span className="flex items-center gap-1"><Eye size={12} /> View-only preview</span>{downloadsAllowed && <button type="button" onClick={download} className="flex items-center gap-1 rounded-md bg-white/10 px-2 py-1 hover:bg-white/20"><Download size={12} /> Download</button>}</div>;
  if (message.file_type === "audio") {
    return (
      <div className="mb-1 min-w-[230px] rounded-xl bg-black/15 p-2">
        <VoicePlayer source={source} message={message} downloadsAllowed={downloadsAllowed} onError={() => setMediaError("Voice message could not be loaded")} />{controls}
      </div>
    );
  }
  if (message.file_type === "image") {
    return (
      <div className="relative mb-1 overflow-hidden rounded-lg"><button type="button" onClick={() => window.open(source, "_blank", "noopener,noreferrer")} className="block w-full cursor-zoom-in" aria-label="Open image preview"><img src={source} alt="Shared attachment" onError={() => setMediaError("Image preview could not be loaded")} className="max-h-64 w-full object-cover" /></button><span className="pointer-events-none absolute inset-0 grid rotate-[-18deg] place-items-center text-sm font-black tracking-[.3em] text-white/25">{watermark}</span>{controls}</div>
    );
  }
  if (message.file_type === "video") {
    return <div className="relative"><video src={source} controls onError={() => setMediaError("Video preview expired or could not be loaded")} controlsList={downloadsAllowed ? undefined : "nodownload"} className="mb-1 max-h-64 w-full rounded-lg" /><span className="pointer-events-none absolute inset-0 grid rotate-[-18deg] place-items-center text-sm font-black tracking-[.3em] text-white/25">{watermark}</span>{controls}</div>;
  }
  return (
    <div className="mb-1 rounded-lg bg-black/15 p-3"><button type="button" onClick={() => window.open(source, "_blank", "noopener,noreferrer")} className="flex items-center gap-3 hover:text-brand-goldLight">
      <span className="grid h-10 w-10 place-items-center rounded-full bg-white/10">
        {message.file_type === "image" ? <Image size={19} /> : <FileText size={19} />}
      </span>
      <span className="text-sm font-medium capitalize">{message.file_type || "File"} attachment</span>
    </button>{controls}</div>
  );
}

export default function ChatPage({ role }) {
  const { requestId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();
  const { socket, connected, reconnecting, connectionError, connectionState, retryConnection, unreadCounts, clearUnread } = useSocket() || {};
  const [request, setRequest] = useState(null);
  const [participant, setParticipant] = useState(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadStage, setUploadStage] = useState("");
  const [finalUploadError, setFinalUploadError] = useState("");
  const [finalMedia, setFinalMedia] = useState(null);
  const [finalProcessing, setFinalProcessing] = useState(false);
  const [finalMetrics, setFinalMetrics] = useState({ loaded: 0, total: 0, bytesPerSecond: 0, etaSeconds: null });
  const [viewOnce, setViewOnce] = useState(false);
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false);
  const imageInputRef = useRef(null);
  const videoInputRef = useRef(null);
  const audioInputRef = useRef(null);
  const documentInputRef = useRef(null);
  const pendingAttachmentRef = useRef(null);
  const uploadAbortRef = useRef(null);
  const textSendLockRef = useRef(false);
  const mediaSendLockRef = useRef(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [otherTyping, setOtherTyping] = useState(false);
  const [participantOnline, setParticipantOnline] = useState(null);
  const [participantLastSeen, setParticipantLastSeen] = useState(null);
  const [recording, setRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [recordingPaused, setRecordingPaused] = useState(false);
  const [voicePreview, setVoicePreview] = useState(null);
  const [voiceStage, setVoiceStage] = useState("selected");
  const bottomRef = useRef(null);
  const typingTimerRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const recordingChunksRef = useRef([]);
  const recordingTimerRef = useRef(null);
  const recordingStartedAtRef = useRef(0);
  const recordingElapsedSecondsRef = useRef(0);
  const recordingStreamRef = useRef(null);
  const cancelRecordingRef = useRef(false);
  const mountedRef = useRef(true);
  const chatReadyRef = useRef({ socket: null, connected: false, joined: false });
  const failedMessagesRef = useRef(new Map());
  const retryingMessagesRef = useRef(new Set());
  const [joined, setJoined] = useState(false);
  const [security, setSecurity] = useState(null);
  const [securityOpen, setSecurityOpen] = useState(false);
  const [projectInfoOpen, setProjectInfoOpen] = useState(false);
  const [paymentStatus, setPaymentStatus] = useState(null);
  const [paymentSubmitting, setPaymentSubmitting] = useState(false);
  const [quote, setQuote] = useState(null);
  const [quoteOpen, setQuoteOpen] = useState(false);
  const [quoteAmount, setQuoteAmount] = useState("");
  const [quoteNote, setQuoteNote] = useState("");
  const [quoteSubmitting, setQuoteSubmitting] = useState(false);
  const [quoteError, setQuoteError] = useState("");
  const [quoteSuccess, setQuoteSuccess] = useState("");
  const [driveLink, setDriveLink] = useState("");
  const [driveSending, setDriveSending] = useState(false);
  const [driveError, setDriveError] = useState("");
  const [driveSuccess, setDriveSuccess] = useState("");
  const [delivery, setDelivery] = useState(null);
  const chatClosed = ["completed", "cancelled", "refunded", "expired", "rejected"].includes(request?.status);
  const paymentOpen = location.pathname.endsWith("/payment") || location.pathname.endsWith("/project");
  const uploadOpen = location.pathname.endsWith("/upload");
  const chatOpen = !paymentOpen && !uploadOpen;
  const chatBasePath = role === "editor" ? `/editor/chat/${requestId}` : `/chat/${requestId}`;

  useEffect(() => {
    chatReadyRef.current = { socket, connected, joined };
  }, [socket, connected, joined]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const loadConversation = useCallback(async (signal) => {
    setLoading(true);
    setError("");
    setRequest(null);
    try {
      const requestResponse = await api.get(`/requests/${requestId}`, { signal });
      if (!requestResponse.data || typeof requestResponse.data !== "object" || Array.isArray(requestResponse.data)) {
        throw new Error("The conversation response was invalid");
      }
      setRequest(requestResponse.data);
      const deliveryResponse = await api.get(`/requests/${requestId}/delivery`, { signal });
      setDelivery(deliveryResponse.data?.delivery || null);
      try {
        const historyResponse = await api.get(`/chat/${requestId}/messages`, { signal });
        setMessages((current) => mergeMessages(current, historyResponse.data?.messages));
        setHasOlderMessages(Boolean(historyResponse.data?.has_more));
      } catch (err) {
        if (err.code === "ERR_CANCELED") return;
        setMessages([]);
        setError(apiErrorMessage(err, "Messages could not be loaded. You can still use this conversation."));
      }
    } catch (err) {
      if (err.code !== "ERR_CANCELED") {
        setError(apiErrorMessage(err, "Unable to load this conversation"));
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [requestId]);

  useEffect(() => {
    const draft = localStorage.getItem(`editzone-chat-draft:${requestId}`);
    if (draft) setText(draft);
  }, [requestId]);

  useEffect(() => {
    let cancelled = false;
    api.get(`/payments/status/${requestId}`)
      .then(({ data }) => { if (!cancelled) setPaymentStatus(data.status); })
      .catch((statusError) => {
        if (!cancelled && statusError.response?.status !== 404) {
          setError(apiErrorMessage(statusError, "Payment status is temporarily unavailable"));
        }
      });
    api.get(`/quotes/project/${requestId}`)
      .then(({ data }) => { if (!cancelled) setQuote(data.quote); })
      .catch(() => { if (!cancelled) setQuote(null); });
    api.get(`/chat/${requestId}/participant`)
      .then(({ data }) => { if (!cancelled) setParticipant(data); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [requestId]);

  useEffect(() => {
    const key = `editzone-chat-draft:${requestId}`;
    if (text) localStorage.setItem(key, text); else localStorage.removeItem(key);
  }, [requestId, text]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  useEffect(() => {
    const controller = new AbortController();
    loadConversation(controller.signal);
    return () => {
      controller.abort();
    };
  }, [loadConversation]);

  useEffect(() => {
    let cancelled = false;
    const loadSecurity = async () => {
      try {
        const response = await api.get(`/uploads/projects/${requestId}/security`);
        if (!cancelled) setSecurity(response.data);
      } catch {
        if (!cancelled) setSecurity(null);
      }
    };
    loadSecurity();
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  const openParticipant = async () => {
    setProfileOpen(true);
    try { const response = await api.get(`/chat/${requestId}/participant`); setParticipant(response.data); }
    catch (err) { setError(apiErrorMessage(err, "Participant profile could not be loaded")); }
  };

  useEffect(() => {
    setJoined(false);
    if (!socket) return undefined;
    const joinConversation = () => {
      // `join_chat` is the established server contract. Keep using it so the
      // page also works while a rolling deployment still has the previous
      // Socket.IO worker; the backend retains `join_conversation` as an alias.
      socket.emit("join_chat", { request_id: requestId }, (result) => {
        if (result?.success) {
          setJoined(!result.chat_closed);
          if (chatOpen && document.visibilityState === "visible") {
            socket.emit("mark_read", { request_id: requestId });
            clearUnread?.(requestId);
          }
          api.get(`/chat/${requestId}/messages`).then((response) => {
            setMessages((current) => mergeMessages(current, response.data?.messages));
            setHasOlderMessages(Boolean(response.data?.has_more));
          }).catch(() => undefined);
        } else if (result?.message) {
          setError(toDisplayText(result.message, "Unable to join this conversation"));
        }
      });
    };
    const onSocketDisconnect = () => {
      setJoined(false);
      setOtherTyping(false);
    };
    const onNewMessage = (message) => {
      if (!message || typeof message !== "object") return;
      if (message.request_id === requestId) {
        setMessages((current) => mergeMessages(current, [message]));
        if (message.sender_id !== user?.id && chatOpen && document.visibilityState === "visible") {
          socket.emit("mark_read", { request_id: requestId });
          clearUnread?.(requestId);
        }
        setOtherTyping(false);
      }
    };
    const onTyping = (data) => {
      if (data.request_id === requestId && data.user_id !== user?.id) {
        setOtherTyping(true);
        clearTimeout(typingTimerRef.current);
        typingTimerRef.current = setTimeout(() => setOtherTyping(false), 1800);
      }
    };
    const onProjectCompleted = (data) => {
      if (data.request_id !== requestId) return;
      setRequest((current) => current && {
        ...current,
        status: "completed",
        payment_status: data.payment_status || "CAPTURED",
      });
      setJoined(false);
      setText("");
      setOtherTyping(false);
    };
    const onPaymentStatus = (data) => {
      if ((data.request_id || data.project_id) !== requestId) return;
      setPaymentStatus(data.payment_status || data.status);
      if (["CAPTURED", "RELEASED"].includes(data.payment_status || data.status)) {
        setRequest((current) => current && { ...current, status: "completed", paid: true, payment_status: "CAPTURED" });
        setJoined(false);
      }
    };
    const onProposalUpdated = (data) => {
      if (String(data?.id || data?._id) === requestId) {
        setRequest(data);
      }
    };
    const onQuoteUpdated = (data) => {
      if (data.project_id === requestId) setQuote(data.quote);
    };
    const onMessagesRead = (data) => {
      if (data.request_id !== requestId || data.reader_id === user?.id) return;
      setMessages((current) => current.map((message) => message.sender_id === user?.id && !message.read_at
        ? { ...message, read_at: data.read_at, delivery_status: "seen" }
        : message));
    };
    const onStoppedTyping = (data) => {
      if (data.request_id === requestId && data.user_id !== user?.id) setOtherTyping(false);
    };
    const onPresence = (data) => {
      if (data.request_id !== requestId || data.user_id === user?.id) return;
      setParticipantOnline(Boolean(data.online));
      if (data.last_seen_at) setParticipantLastSeen(data.last_seen_at);
    };
    const onViewOnceOpened = (data) => {
      if (data.request_id !== requestId) return;
      setMessages((current) => current.map((message) => String(message.id || message._id) === String(data.message_id) ? { ...message, viewed_at: data.viewed_at, view_once_status: "opened" } : message));
    };
    const onChatError = (data) => {
      if (["PHONE_NUMBER_NOT_ALLOWED", "CONTACT_LINK_NOT_ALLOWED"].includes(data?.code)) {
        setError(PHONE_BLOCK_MESSAGE);
      } else if (data?.message) {
        setError(toDisplayText(data.message, "Message could not be sent"));
      }
    };
    socket.on("connect", joinConversation);
    socket.on("disconnect", onSocketDisconnect);
    socket.on("new_message", onNewMessage);
    socket.on("user_typing", onTyping);
    socket.on("project_completed", onProjectCompleted);
    socket.on("payment_status_updated", onPaymentStatus);
    socket.on("proposal_updated", onProposalUpdated);
    socket.on("quote_updated", onQuoteUpdated);
    socket.on("project_quote_created", onQuoteUpdated);
    socket.on("project_payment_updated", onPaymentStatus);
    socket.on("messages_read", onMessagesRead);
    socket.on("user_stopped_typing", onStoppedTyping);
    socket.on("presence_status", onPresence);
    socket.on("view_once_opened", onViewOnceOpened);
    socket.on("chat_error", onChatError);
    if (socket.connected) joinConversation();
    return () => {
      // Never buffer a leave event while disconnected: it could be replayed
      // immediately after the reconnect join and silently remove this client
      // from the room. A dead Socket.IO session leaves rooms server-side.
      if (socket.connected) socket.emit("leave_chat", { request_id: requestId });
      socket.off("connect", joinConversation);
      socket.off("disconnect", onSocketDisconnect);
      socket.off("new_message", onNewMessage);
      socket.off("user_typing", onTyping);
      socket.off("project_completed", onProjectCompleted);
      socket.off("payment_status_updated", onPaymentStatus);
      socket.off("proposal_updated", onProposalUpdated);
      socket.off("quote_updated", onQuoteUpdated);
      socket.off("project_quote_created", onQuoteUpdated);
      socket.off("project_payment_updated", onPaymentStatus);
      socket.off("messages_read", onMessagesRead);
      socket.off("user_stopped_typing", onStoppedTyping);
      socket.off("presence_status", onPresence);
      socket.off("view_once_opened", onViewOnceOpened);
      socket.off("chat_error", onChatError);
      clearTimeout(typingTimerRef.current);
      setJoined(false);
    };
  }, [socket, requestId, user?.id, chatOpen, clearUnread]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, otherTyping]);

  useEffect(() => {
    return () => {
      clearInterval(recordingTimerRef.current);
      recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
        cancelRecordingRef.current = true;
        mediaRecorderRef.current.stop();
      }
    };
  }, []);

  useEffect(() => () => {
    if (voicePreview?.url) URL.revokeObjectURL(voicePreview.url);
  }, [voicePreview?.url]);

  const updateText = (value) => {
    if (value.length > MAX_TEXT_MESSAGE_LENGTH) {
      setError(`Message must be ${MAX_TEXT_MESSAGE_LENGTH.toLocaleString()} characters or fewer.`);
      return;
    }
    setText(value);
    if (!chatClosed && value.trim() && socket && connected && joined) socket.emit("typing", { request_id: requestId });
    else if (socket && connected && joined) socket.emit("typing_stop", { request_id: requestId });
  };
  const openPaymentPage = async () => {
    if (paymentSubmitting) return;
    setPaymentSubmitting(true);
    navigate(`/payment/checkout/${encodeURIComponent(quote.id)}`);
  };

  const sendFinalAmount = async (event) => {
    event.preventDefault();
    if (quoteSubmitting) return;
    setQuoteSubmitting(true); setQuoteError("");
    try {
      const { data } = await api.post(`/projects/${requestId}/final-quote`, { amount: quoteAmount, note: quoteNote || null });
      setQuote(data); setQuoteOpen(false); setQuoteAmount(""); setQuoteNote("");
      setQuoteSuccess("Final project price sent to the client.");
    } catch (err) { setQuoteError(apiErrorMessage(err, "Final amount could not be sent")); }
    finally { setQuoteSubmitting(false); }
  };

  const loadOlderMessages = async () => {
    const oldest = messages[0]?.created_at;
    if (!oldest || loadingOlder || !hasOlderMessages) return;
    setLoadingOlder(true);
    try {
      const response = await api.get(`/chat/${requestId}/messages`, { params: { before: oldest, limit: 50 } });
      setMessages((current) => mergeMessages(response.data?.messages, current));
      setHasOlderMessages(Boolean(response.data?.has_more));
    } catch (err) { setError(apiErrorMessage(err, "Older messages could not be loaded")); }
    finally { setLoadingOlder(false); }
  };

  const waitForChatReady = useCallback(async (timeoutMs = 15000) => {
    const deadline = Date.now() + timeoutMs;
    while (mountedRef.current && Date.now() < deadline) {
      const state = chatReadyRef.current;
      if (state.socket?.connected && state.connected && state.joined) return state.socket;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    throw new Error("Message could not be sent while chat was offline.");
  }, []);

  const emitMessage = useCallback(async (rawPayload, { optimistic = true } = {}) => {
    const clientMessageId = rawPayload.client_message_id || newClientMessageId();
    const payload = { ...rawPayload, client_message_id: clientMessageId };
    delete payload.optimistic_file_url;
    if (optimistic) {
      setMessages((current) => mergeMessages(current, [{
        request_id: requestId,
        sender_id: user?.id,
        text: payload.text || null,
        file_url: rawPayload.optimistic_file_url || null,
        file_type: payload.file_type || null,
        client_message_id: clientMessageId,
        created_at: new Date().toISOString(),
        delivery_status: "pending",
        local_only: true,
      }]));
    } else {
      setMessages((current) => current.map((message) => message.client_message_id === clientMessageId ? { ...message, delivery_status: "pending" } : message));
    }
    failedMessagesRef.current.set(clientMessageId, rawPayload);
    let lastError;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const activeSocket = await waitForChatReady();
        const result = await new Promise((resolve, reject) => {
          activeSocket.timeout(12000).emit("send_message", { request_id: requestId, ...payload }, (timeoutError, response) => {
            if (timeoutError) reject(new Error("Message acknowledgement timed out."));
            else if (!response?.success) reject(Object.assign(new Error(response?.message || "Message could not be sent."), { permanent: response?.code !== "TEMPORARY_UNAVAILABLE" }));
            else resolve(response);
          });
        });
        if (result.message) setMessages((current) => mergeMessages(current, [{ ...result.message, delivery_status: result.message.delivery_status || "sent", local_only: false }]));
        failedMessagesRef.current.delete(clientMessageId);
        return result;
      } catch (sendError) {
        lastError = sendError;
        if (sendError.permanent) break;
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(1000 * (2 ** attempt), 4000)));
      }
    }
    setMessages((current) => current.map((message) => message.client_message_id === clientMessageId ? { ...message, delivery_status: "failed" } : message));
    throw lastError || new Error("Message could not be sent.");
  }, [requestId, user?.id, waitForChatReady]);

  const retryFailedMessage = useCallback(async (clientMessageId) => {
    const payload = failedMessagesRef.current.get(clientMessageId);
    if (!payload || retryingMessagesRef.current.has(clientMessageId)) return;
    retryingMessagesRef.current.add(clientMessageId);
    try {
      await emitMessage(payload, { optimistic: false });
    } catch {
      // The failed bubble remains the non-intrusive retry surface.
    } finally {
      retryingMessagesRef.current.delete(clientMessageId);
    }
  }, [emitMessage]);

  const sendText = async (event) => {
    event.preventDefault();
    const message = text.trim();
    if (chatClosed || !message || sending || textSendLockRef.current) return;
    if (containsPhoneNumber(message)) {
      setError(PHONE_BLOCK_MESSAGE);
      return;
    }
    textSendLockRef.current = true;
    setSending(true);
    setError("");
    const clientMessageId = newClientMessageId();
    setText("");
    try {
      await emitMessage({ text: message, client_message_id: clientMessageId });
    } catch (err) {
      if (err?.permanent) setError(toDisplayText(err?.message, "Message could not be sent"));
    } finally {
      textSendLockRef.current = false;
      setSending(false);
    }
  };

  const upload = async (file, purpose = "chat_attachment", onProgress, options = {}) => {
    const body = new FormData();
    body.append("file", file);
    body.append("request_id", requestId);
    body.append("purpose", purpose);
    if (options.category) body.append("category", options.category);
    if (options.viewOnce) body.append("view_once", "true");
    uploadAbortRef.current = new AbortController();
    return secureUpload(body, { onProgress, onMetrics: options.onMetrics, onStage: options.onStage, onProcessing: options.onProcessing, directUploadMinMb: security?.direct_upload_min_mb || 25, signal: uploadAbortRef.current.signal });
  };

  const handleFile = (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file || chatClosed) return;
    const category = fileCategory(file);
    if (!category) {
      setError("This file format is not supported in chat.");
      event.target.value = "";
      return;
    }
    if (category === "image") {
      const extension = file.name.split(".").pop()?.toLowerCase();
      if (!new Set(["jpg", "jpeg", "png", "webp"]).has(extension) || !new Set(["image/jpeg", "image/png", "image/webp"]).has(file.type)) {
        setError("Chat images must be JPG, JPEG, PNG, or WebP files.");
        event.target.value = "";
        return;
      }
    }
    const validation = validateFileSize(file, category);
    if (!validation.valid) {
      setError(validation.message);
      event.target.value = "";
      return;
    }
    const allowedVideoTypes = security?.chat_video_mime_types || ["video/mp4", "video/webm", "video/quicktime"];
    if (file.type.startsWith("video/") && !allowedVideoTypes.includes(file.type)) {
      setError("Video format not supported. Choose an MP4, WebM, or MOV file.");
      event.target.value = "";
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setSelectedFile(file);
    setViewOnce(false);
    setPreviewUrl(file.type.startsWith("video/") || file.type.startsWith("image/") || file.type.startsWith("audio/") ? URL.createObjectURL(file) : "");
    setUploadProgress(0);
    setUploadStage("selected");
    pendingAttachmentRef.current = { clientMessageId: newClientMessageId() };
    setError("");
    event.target.value = "";
  };

  const clearSelectedFile = () => {
    if (uploading) uploadAbortRef.current?.abort();
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setSelectedFile(null); setPreviewUrl(""); setUploadProgress(0); setUploadStage("");
    setViewOnce(false);
    pendingAttachmentRef.current = null;
  };

  const sendSelectedFile = async () => {
    if (!selectedFile || uploading || mediaSendLockRef.current) return;
    mediaSendLockRef.current = true;
    setUploading(true);
    setError("");
    try {
      const caption = text.trim();
      if (caption && containsPhoneNumber(caption)) throw new Error(PHONE_BLOCK_MESSAGE);
      const category = fileCategory(selectedFile, { viewOnce });
      const validation = validateFileSize(selectedFile, category);
      if (!validation.valid) throw new Error(validation.message);
      let response = pendingAttachmentRef.current?.uploadResponse;
      if (!response) {
        setUploadStage("uploading");
        response = await upload(selectedFile, "chat_attachment", setUploadProgress, { category, viewOnce, onProcessing: () => setUploadStage("processing") });
        pendingAttachmentRef.current.uploadResponse = response;
      } else {
        setUploadStage("processing");
        await waitForUploadScan(response.data.upload_id);
      }
      setUploadStage("sending");
      await emitMessage({ text: caption, upload_id: response.data.upload_id, file_type: response.data.file_type, view_once: viewOnce, client_message_id: pendingAttachmentRef.current.clientMessageId, optimistic_file_url: previewUrl });
      if (caption) setText("");
      setUploadStage("sent");
      clearSelectedFile();
    } catch (err) {
      if (pendingAttachmentRef.current) {
        // A scan/storage failure is retried with the original File in a fresh
        // request. Never keep polling a terminal upload or reuse an expired
        // multipart URL. The stable client message ID prevents duplicates.
        const saved = pendingAttachmentRef.current.uploadResponse;
        pendingAttachmentRef.current.uploadResponse = saved?.data?.status === "ready"
          || saved?.data?.scan_status === "safe"
          || err?.code === "MEDIA_PROCESSING"
          ? (saved || err.uploadResponse)
          : null;
      }
      uploadAbortRef.current = null;
      setUploadProgress(0);
      setUploadStage("failed");
      setError(err?.code === "ERR_CANCELED" ? "Upload interrupted. Select Retry to continue." : apiErrorMessage(err, "The backend is unavailable or the upload was interrupted."));
    } finally {
      mediaSendLockRef.current = false;
      setUploading(false);
    }
  };

  const uploadVoiceMessage = async (preview) => {
    const { blob, mimeType, duration, clientMessageId } = preview;
    if (chatClosed || !blob.size) return false;
    if (agreementBlocked) {
      setError("Accept the editor media agreement before sending voice messages.");
      return false;
    }
    setUploading(true);
    setVoiceStage("uploading");
    setUploadProgress(0);
    setError("");
    try {
      const extension = mimeType.includes("ogg") ? "ogg" : mimeType.includes("mp4") ? "m4a" : "webm";
      const file = new File([blob], `voice-message-${Date.now()}.${extension}`, { type: mimeType });
      const validation = validateFileSize(file, "voice");
      if (!validation.valid) throw new Error(validation.message);
      let response = preview.uploadResponse;
      if (!response) {
        response = await upload(file, "chat_attachment", setUploadProgress, { category: "voice", onProcessing: () => setVoiceStage("processing") });
        setVoicePreview((current) => current?.clientMessageId === clientMessageId ? { ...current, uploadResponse: response } : current);
      } else {
        await waitForUploadScan(response.data.upload_id);
      }
      await emitMessage({ upload_id: response.data.upload_id, file_type: "audio", duration_seconds: duration, client_message_id: clientMessageId, optimistic_file_url: preview.url });
      setVoiceStage("sent");
      return true;
    } catch (err) {
      if (err?.uploadResponse) {
        setVoicePreview((current) => current?.clientMessageId === clientMessageId
          ? { ...current, uploadResponse: err.code === "MEDIA_PROCESSING" ? err.uploadResponse : null }
          : current);
      }
      uploadAbortRef.current = null;
      setUploadProgress(0);
      setError(apiErrorMessage(err, "Voice message upload was interrupted or the storage service rejected it."));
      setVoiceStage("failed");
      return false;
    } finally {
      setUploading(false);
    }
  };

  const stopRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      try { recorder.requestData(); } catch { /* Some browsers flush automatically. */ }
      recorder.stop();
    }
  };

  const cancelRecording = () => {
    cancelRecordingRef.current = true;
    stopRecording();
  };

  const toggleRecordingPause = () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    if (recorder.state === "paused") { recorder.resume(); setRecordingPaused(false); }
    else { recorder.pause(); setRecordingPaused(true); }
  };

  const discardVoicePreview = () => {
    if (voicePreview?.url) URL.revokeObjectURL(voicePreview.url);
    setVoicePreview(null);
    setVoiceStage("selected");
    setUploadProgress(0);
  };

  const sendVoicePreview = async () => {
    if (!voicePreview || mediaSendLockRef.current) return;
    mediaSendLockRef.current = true;
    try {
      const sent = await uploadVoiceMessage(voicePreview);
      if (sent) discardVoicePreview();
    } finally {
      mediaSendLockRef.current = false;
    }
  };

  const startRecording = async () => {
    if (chatClosed || uploading || recording) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Voice recording is not supported by this browser");
      return;
    }
    setError("");
    let stream;
    try {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: VOICE_CONSTRAINTS });
      } catch (preferredError) {
        if (["NotAllowedError", "NotFoundError", "SecurityError"].includes(preferredError.name)) throw preferredError;
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      }
      const preferredTypes = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/ogg", "audio/mp4"];
      const mimeType = preferredTypes.find((type) => MediaRecorder.isTypeSupported(type)) || "";
      const recorder = new MediaRecorder(stream, {
        ...(mimeType ? { mimeType } : {}),
        audioBitsPerSecond: VOICE_AUDIO_BITS_PER_SECOND,
      });
      recordingStreamRef.current = stream;
      cancelRecordingRef.current = false;
      recordingChunksRef.current = [];
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => event.data.size && recordingChunksRef.current.push(event.data);
      recorder.onerror = () => {
        setError("Voice recording stopped unexpectedly");
        stream.getTracks().forEach((track) => track.stop());
        recordingStreamRef.current = null;
        mediaRecorderRef.current = null;
        clearInterval(recordingTimerRef.current);
        setRecording(false);
        setRecordingSeconds(0);
      };
      recorder.onstop = () => {
        const recordedType = recorder.mimeType || mimeType || "audio/webm";
        const blob = new Blob(recordingChunksRef.current, { type: recordedType });
        stream.getTracks().forEach((track) => track.stop());
        recordingStreamRef.current = null;
        mediaRecorderRef.current = null;
        recordingChunksRef.current = [];
        clearInterval(recordingTimerRef.current);
        setRecording(false);
        setRecordingPaused(false);
        setRecordingSeconds(0);
        if (!cancelRecordingRef.current && blob.size) {
          const validation = validateFileSize(blob, "voice");
          if (!validation.valid) setError(validation.message);
          else {
            setVoiceStage("selected");
            setVoicePreview({ blob, mimeType: recordedType, duration: Math.min(VOICE_MAX_SECONDS, Math.max(1, recordingElapsedSecondsRef.current)), url: URL.createObjectURL(blob), clientMessageId: newClientMessageId(), uploadResponse: null });
          }
        }
        cancelRecordingRef.current = false;
      };
      recorder.start(250);
      recordingStartedAtRef.current = Date.now();
      recordingElapsedSecondsRef.current = 0;
      setRecording(true);
      setRecordingPaused(false);
      setRecordingSeconds(0);
      recordingTimerRef.current = setInterval(() => setRecordingSeconds((value) => {
        if (mediaRecorderRef.current?.state === "paused") return value;
        recordingElapsedSecondsRef.current = value + 1;
        if (value + 1 >= VOICE_MAX_SECONDS) {
          setTimeout(stopRecording, 0);
          return VOICE_MAX_SECONDS;
        }
        return value + 1;
      }), 1000);
    } catch (err) {
      stream?.getTracks().forEach((track) => track.stop());
      recordingStreamRef.current = null;
      mediaRecorderRef.current = null;
      setError(err.name === "NotAllowedError" ? "Microphone permission was denied. Allow microphone access and try again." : err.name === "NotFoundError" ? "No microphone is available." : "Unable to start voice recording.");
    }
  };

  const submitFinalDelivery = async (media = finalMedia) => {
    if (!media?.upload_id) return;
    setFinalProcessing(true);
    setUploadStage("processing");
    setFinalUploadError("");
    try {
      // Re-read the authoritative scan state immediately before creating the
      // delivery. This closes the race between upload completion/background
      // scanning and the delivery endpoint's strict `safe` requirement.
      await retryUploadScan(media.upload_id, 90000, uploadAbortRef.current?.signal);
      const deliveryResponse = await api.post(`/requests/${requestId}/deliver`, { upload_id: media.upload_id, delivery_message: media.deliveryMessage || "Your final edited video is ready." });
      setDelivery(deliveryResponse.data?.delivery || null);
      if (deliveryResponse.data?.project) setRequest(deliveryResponse.data.project);
      else {
        const requestResponse = await api.get(`/requests/${requestId}`);
        setRequest(requestResponse.data);
      }
      setUploadProgress(100);
      setUploadStage("submitted");
      setFinalMedia(null);
    } catch (err) {
      setUploadStage("ready");
      setFinalUploadError(apiErrorMessage(err, "Final output was uploaded, but submission failed. Retry submission."));
    } finally {
      setFinalProcessing(false);
    }
  };

  const deliverFinal = async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file || mediaSendLockRef.current) return;
    if (!["accepted", "in_progress", "overdue", "revision_requested"].includes(request?.status)) {
      return;
    }
    if (!file.size || file.size > FINAL_OUTPUT_MAX_BYTES || !["video/mp4", "video/webm", "video/quicktime"].includes(file.type)) {
      setFinalUploadError(!file.size || file.size > FINAL_OUTPUT_MAX_BYTES ? "Final output must be a non-empty video no larger than 1,000,000,000 bytes (1000 MB)." : "Final output must be an MP4, WebM, or MOV video.");
      return;
    }
    mediaSendLockRef.current = true;
    setUploading(true);
    setUploadProgress(0);
    setUploadStage("uploading");
    setFinalUploadError("");
    try {
      const response = await upload(file, "final_delivery", setUploadProgress, { onMetrics: setFinalMetrics, onStage: setUploadStage, onProcessing: (saved) => { setFinalMedia({ ...saved, filename: file.name, size: file.size }); setUploading(false); setFinalProcessing(true); setUploadStage("processing"); } });
      const media = { ...response.data, filename: file.name, size: file.size };
      setFinalMedia(media);
      setUploadStage("ready");
      await submitFinalDelivery(media);
    } catch (err) {
      const saved = err.uploadResponse?.data;
      if (saved?.upload_id) setFinalMedia({ ...saved, filename: file.name, size: file.size });
      setUploadStage(saved?.upload_id ? "processing" : err.code === "ERR_CANCELED" ? "cancelled" : "failed");
      setFinalUploadError(err.code === "ERR_CANCELED" ? "Upload was cancelled." : apiErrorMessage(err, "Failed to upload the final file"));
    } finally {
      mediaSendLockRef.current = false;
      setUploading(false);
      setFinalProcessing(false);
    }
  };

  const cancelFinalUpload = async () => {
    uploadAbortRef.current?.abort();
    const uploadId = finalMedia?.upload_id;
    setUploading(false); setFinalProcessing(false); setUploadProgress(0); setFinalMetrics({ loaded: 0, total: 0, bytesPerSecond: 0, etaSeconds: null }); setUploadStage("CANCELLED"); setFinalUploadError("Upload was cancelled.");
    if (uploadId) await api.delete(`/media/${encodeURIComponent(uploadId)}`).catch(() => undefined);
    setFinalMedia(null);
  };

  if (loading) return <div className="min-h-screen bg-black/25"><Loader label="Opening conversation..." /></div>;
  if (error && !request) return (
    <div className="grid min-h-screen place-items-center bg-black/25 px-6">
      <div className="glass motion-panel w-full max-w-md rounded-2xl p-8 text-center">
        <p className="text-red-300">{toDisplayText(error, "Unable to load this conversation")}</p>
        <div className="mt-5 flex justify-center gap-3">
          <button type="button" onClick={() => loadConversation()} className="rounded-lg bg-brand-gradient px-4 py-2 text-sm font-semibold text-white">Try Again</button>
          <button type="button" onClick={() => navigate(role === "editor" ? "/editor/dashboard" : "/order-history")} className="rounded-lg border border-brand-border px-4 py-2 text-sm text-gray-300">Go Back</button>
        </div>
      </div>
    </div>
  );
  if (!request) return null;

  const isEditor = role === "editor";
  const backPath = isEditor ? "/editor/dashboard" : "/order-history";
  const projectTitle = toDisplayText(request.project_title, "EditZone Project");
  const requestStatus = toDisplayText(request.status, "unknown");
  const initials = projectTitle.slice(0, 2).toUpperCase();
  const isProjectOwner = !isEditor && String(request.user_id || "") === String(user?.id || "");
  const paymentCompleted = quote?.status === "PAID" || Boolean(request.paid)
    || ["SUCCESS", "CAPTURED", "RELEASED"].includes(paymentStatus);
  const paymentPending = quote?.status === "PAYMENT_PENDING" || ["INITIATED", "PENDING"].includes(paymentStatus);
  const paymentFailed = ["CANCELLED", "FAILED", "CHARGEDBACK"].includes(paymentStatus);
  const requestAccepted = ["accepted", "payment_failed"].includes(request.status)
    || Boolean(request.accepted_at || request.responded_at && request.status !== "rejected");
  const payableAmount = Number(quote?.client_total || 0);
  const deliveryPayable = ["READY_FOR_PAYMENT", "PAYMENT_FAILED"].includes(delivery?.delivery_status);
  const showPayNow = isProjectOwner && quote?.status === "SENT" && Number.isFinite(payableAmount) && payableAmount > 0 && !chatClosed && !paymentCompleted && !paymentPending;
  const canSetFinalAmount = isEditor && String(request.editor_user_id || "") === String(user?.id || "") && requestAccepted && joined && !chatClosed && !paymentCompleted && !paymentPending;
  const visibleError = toDisplayText(error || connectionError);
  const agreementBlocked = isEditor && security && !security.agreement_accepted;
  const downloadsAllowed = !isEditor || Boolean(security?.editor_download_allowed);
  const openFinalDelivery = async (mode = "preview") => {
    if (!delivery?.upload_id) return;
    try {
      const url = await protectedMediaUrl(delivery.access_path, mode);
      window.open(url, "_blank", "noopener,noreferrer");
    } catch (err) {
      setError(apiErrorMessage(err, "Secure final video access is unavailable"));
    }
  };

  const acceptAgreement = async () => {
    await api.post(`/uploads/projects/${requestId}/agreement`);
    setSecurity((value) => ({ ...value, agreement_accepted: true }));
  };
  const toggleDownloads = async () => {
    const next = !security?.editor_download_allowed;
    const response = await api.put(`/uploads/projects/${requestId}/policy`, { editor_download_allowed: next, retention_days: security?.retention_days || 30 });
    setSecurity((value) => ({ ...value, ...response.data }));
  };
  const reportMisuse = async () => {
    const details = window.prompt("Describe the suspected misuse (minimum 10 characters):");
    if (!details) return;
    await api.post(`/uploads/projects/${requestId}/report`, { reason: "other", details });
    setError("Misuse report submitted for admin review.");
  };
  const reportConversation = async () => {
    const reason = window.prompt("Report reason: spam, harassment, fraud, inappropriate_content, off_platform_contact, file_misuse, copyright, or other");
    if (!reason) return;
    const description = window.prompt("Describe what happened (optional):") || "";
    try {
      await api.post(`/chat/${requestId}/reports`, { reason: reason.trim().toLowerCase(), description, message_ids: [] });
      setError("Report submitted for admin review.");
    } catch (err) { setError(apiErrorMessage(err, "Report could not be submitted")); }
  };
  const lifecycleReason = async (path, method = "post") => {
    const reason = window.prompt("Give a clear reason (minimum 10 characters):");
    if (!reason || reason.trim().length < 10) return;
    try {
      const response = await api[method](`/requests/${requestId}/${path}`, { reason });
      setRequest(response.data);
      setError("");
    } catch (err) {
      setError(apiErrorMessage(err, "Project action failed"));
    }
  };
  const decideCancellation = async (approve) => {
    try {
      const response = await api.patch(`/requests/${requestId}/cancel`, { approve, reason: approve ? "Cancellation accepted by the other project member" : "Cancellation declined; project work should continue" });
      setRequest(response.data);
    } catch (err) {
      setError(apiErrorMessage(err, "Cancellation decision failed"));
    }
  };
  const acceptRevision = async () => {
    try {
      const response = await api.post(`/requests/${requestId}/revision/accept`);
      setRequest(response.data);
    } catch (err) {
      setError(apiErrorMessage(err, "Revision could not be accepted"));
    }
  };
  const processRefund = async () => {
    const reason = window.prompt("Confirm the refund reason:");
    if (!reason || reason.trim().length < 5) return;
    try {
      await api.post(`/payments/${requestId}/refund`, { reason });
      const response = await api.get(`/requests/${requestId}`);
      setRequest(response.data);
    } catch (err) {
      setError(apiErrorMessage(err, "Refund processing failed"));
    }
  };
  const negotiate = async (counter = false) => {
    const amount = Number(window.prompt("Offer amount (LKR):", request.proposal_amount || request.brief?.budget_max || ""));
    const deliveryDays = Number(window.prompt("Delivery days:", request.proposal_delivery_days || "7"));
    const revisions = Number(window.prompt("Included revisions:", request.proposal_revision_limit ?? request.brief?.requested_revision_limit ?? 2));
    const message = window.prompt("Proposal details (minimum 10 characters):", request.proposal_message || "Includes editing and agreed deliverables.");
    if (!amount || !deliveryDays || !message || message.trim().length < 10) return;
    try {
      const response = await api.post(`/requests/${requestId}/${counter ? "counter-offer" : "proposal"}`, { amount, delivery_days: deliveryDays, included_revisions: revisions, message });
      setRequest(response.data);
    } catch (err) { setError(apiErrorMessage(err, "Proposal could not be saved")); }
  };
  const acceptProposal = async () => {
    try {
      await api.post(`/requests/${requestId}/proposal/accept`);
      const response = await api.get(`/requests/${requestId}`);
      setRequest(response.data);
    }
    catch (err) { setError(apiErrorMessage(err, "Proposal acceptance failed")); }
  };
  const timestampFeedback = async () => {
    const raw = window.prompt("Video timestamp (MM:SS or seconds):");
    if (!raw) return;
    const parts = raw.split(":").map(Number);
    const seconds = parts.length === 2 ? parts[0] * 60 + parts[1] : Number(raw);
    const note = window.prompt("Feedback at this timestamp:");
    if (!Number.isInteger(seconds) || seconds < 0 || !note?.trim()) return;
    try { await emitMessage({ text: note.trim(), timecode_seconds: seconds, client_message_id: newClientMessageId() }); }
    catch (err) { setError(toDisplayText(err?.message, "Timestamp feedback failed")); }
  };

  const sendDriveLink = async (event) => {
    event.preventDefault();
    if (driveSending || chatClosed) return;
    setDriveError("");
    setDriveSuccess("");
    let parsed;
    try { parsed = new URL(driveLink.trim()); } catch { setDriveError("Enter a valid Google Drive link."); return; }
    if (parsed.protocol !== "https:" || !["drive.google.com", "docs.google.com"].includes(parsed.hostname.toLowerCase())) {
      setDriveError("Enter a valid Google Drive link.");
      return;
    }
    setDriveSending(true);
    try {
      await emitMessage({ text: parsed.href, client_message_id: newClientMessageId() });
      setDriveLink("");
      setDriveSuccess("Drive link shared successfully. It is now available in Chat.");
    } catch (err) {
      setDriveError(toDisplayText(err?.message, "Unable to send the Drive link. Please try again."));
    } finally { setDriveSending(false); }
  };

  return (
    <div className="chat-shell flex h-dvh flex-col text-[#f5f5f5]">
      <header className="liquid-chat-bar z-10 m-2 flex min-h-[72px] items-center justify-between gap-3 rounded-2xl px-3 shadow-xl sm:m-3 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <button onClick={() => navigate(backPath)} aria-label="Back" className="rounded-full p-2 text-[#a7a7a7] hover:bg-white/10"><ArrowLeft size={21} /></button>
          <button type="button" onClick={openParticipant} aria-label="Open participant profile" className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-avatar-gradient font-bold text-brand-goldWarm shadow-lg shadow-brand-gold/20">{initials}</button>
          <button type="button" onClick={openParticipant} className="min-w-0 text-left">
            <p className="truncate rounded-md font-semibold text-white">{projectTitle}</p>
            <p className={`text-xs ${connected ? "text-[#8e8e8e]" : "text-amber-300"}`}>
              {chatClosed ? "project completed · chat closed" : otherTyping ? <span className="text-brand-goldLight">typing…</span> : joined ? `${participantOnline === true ? "Online" : participantOnline === false && participantLastSeen ? `Last seen ${formatTime(participantLastSeen)}` : "Connected"} · ${requestStatus}` : connected ? "joining chat…" : reconnecting ? "Reconnecting…" : "Offline"}
            </p>
          </button>
        </div>
      </header>
      <div className="workspace-body flex min-h-0 flex-1">
      <nav aria-label="Project workspace" className="workspace-nav m-3 mt-0 flex w-60 shrink-0 flex-col rounded-2xl border border-brand-gold/20 bg-[#0d0d0d]/90 p-3 backdrop-blur-xl">
        <p className="px-3 py-2 text-[11px] font-bold uppercase tracking-[.18em] text-brand-gold">EZ / Project</p>
        <button type="button" onClick={() => navigate(chatBasePath)} className={`workspace-nav-item ${chatOpen ? "is-active" : ""}`}><MessageSquare size={18} />Chat{Number(unreadCounts?.[requestId] || 0) > 0 && <span className="workspace-unread-badge">{unreadCounts[requestId] > 99 ? "99+" : unreadCounts[requestId]}</span>}</button>
        <button type="button" onClick={() => navigate(`${chatBasePath}/payment`)} className={`workspace-nav-item ${paymentOpen ? "is-active" : ""}`}><WalletCards size={18} />Payment Workshop</button>
        <button type="button" onClick={() => navigate(`${chatBasePath}/upload`)} className={`workspace-nav-item ${uploadOpen ? "is-active" : ""}`}><UploadCloud size={18} />Upload</button>
        <div className="mt-auto border-t border-white/10 px-3 pt-4 text-xs"><p className="truncate font-semibold text-white">{projectTitle}</p><p className="mt-1 capitalize text-emerald-300">{requestStatus.replaceAll("_", " ")}</p></div>
      </nav>
      <div className="workspace-content flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className={paymentOpen ? "workspace-scroll flex-1 overflow-y-auto py-2" : "hidden"}>
      <section className="mx-3 mb-4"><p className="text-xs font-bold uppercase tracking-[.18em] text-brand-gold">Payment Workshop</p><h1 className="mt-1 text-2xl font-bold">Project payment and fee details</h1><p className="mt-1 text-sm text-slate-400">Secure quotes and PayHere payment for this project.</p></section>
      {paymentOpen && quote && <section className="mx-3 mb-2 max-w-3xl rounded-2xl border border-brand-gold/25 bg-[#111]/95 p-5 shadow-xl" aria-label="Final project payment">
        <div className="flex items-start justify-between gap-4"><div className="flex gap-3"><span className="grid h-11 w-11 place-items-center overflow-hidden rounded-full bg-avatar-gradient font-bold text-brand-gold">{participant?.profile_picture ? <img src={participant.profile_picture} alt="" className="h-full w-full object-cover" /> : (participant?.display_name || "ED").slice(0, 2).toUpperCase()}</span><div><p className="text-[11px] font-bold uppercase tracking-wider text-brand-gold">{isEditor ? "Final price sent to client" : "Final price set by editor"}</p><h2 className="font-semibold">{projectTitle}</h2><p className="text-xs text-slate-400">{participant?.display_name || "Assigned editor"}</p></div></div><span className="rounded-full bg-white/5 px-2 py-1 text-xs">{quote.status.replaceAll("_", " ")}</span></div>
        <dl className="mt-4 space-y-2 text-sm"><div className="flex justify-between"><dt>Project amount</dt><dd>LKR {quote.project_amount}</dd></div><div className="flex justify-between text-slate-400"><dt>EditZone client service fee (10%)</dt><dd>LKR {quote.client_service_fee}</dd></div><div className="flex justify-between border-t border-white/10 pt-3 text-lg font-bold"><dt>Total payable</dt><dd>LKR {quote.client_total}</dd></div></dl>
        {quote.note && <p className="mt-3 rounded-lg bg-white/[.04] p-3 text-sm text-slate-300">{quote.note}</p>}
        {quote.expires_at && <p className="mt-2 text-xs text-slate-500">Expires {new Date(quote.expires_at).toLocaleString("en-LK")}</p>}
        <p className="mt-3 flex items-center gap-2 text-xs text-emerald-200"><ShieldCheck size={15} /> You save no card details on EditZone</p>
        {showPayNow && <button type="button" onClick={openPaymentPage} disabled={paymentSubmitting} className="chat-action mt-4 flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 font-semibold disabled:opacity-60"><CreditCard size={18} />{paymentSubmitting ? "Preparing payment…" : paymentFailed ? "Try payment again" : "Pay Securely"}</button>}
        {paymentCompleted && <p className="mt-4 rounded-xl bg-emerald-400/10 p-3 text-center font-semibold text-emerald-200">Payment completed</p>}
        {quote.status === "EXPIRED" && <p className="mt-4 rounded-xl bg-amber-400/10 p-3 text-center text-amber-200">This payment request expired. Ask the editor to issue a new one.</p>}
      </section>}
      {quoteOpen && isEditor && <div className="fixed inset-0 z-[80] grid place-items-center bg-black/75 p-4" role="dialog" aria-modal="true" aria-labelledby="quote-title"><form onSubmit={sendFinalAmount} className="w-full max-w-lg rounded-2xl border border-brand-gold/25 bg-[#101010] p-6 shadow-2xl"><p className="text-xs font-bold uppercase tracking-wider text-brand-gold">LKR · Final project quote</p><h2 id="quote-title" className="mt-1 text-xl font-bold">Set Client Amount</h2><div className="mt-4 grid gap-3 text-sm sm:grid-cols-2"><p><span className="block text-slate-500">Project</span>{projectTitle}</p><p><span className="block text-slate-500">Client</span>{participant?.display_name || participant?.username || "Project client"}</p></div><label className="mt-5 block text-sm font-semibold">Final project amount · LKR<input autoFocus required inputMode="decimal" type="number" min="100" max="10000000" step="0.01" value={quoteAmount} onChange={(e) => setQuoteAmount(e.target.value)} className="mt-2 w-full rounded-xl border border-white/15 bg-black/40 px-4 py-3 outline-none focus:border-brand-gold" /></label><label className="mt-3 block text-sm font-semibold">Payment note (optional)<textarea maxLength={500} value={quoteNote} onChange={(e) => setQuoteNote(e.target.value)} className="mt-2 min-h-20 w-full rounded-xl border border-white/15 bg-black/40 px-4 py-3 outline-none focus:border-brand-gold" /></label>{Number(quoteAmount) > 0 && <dl className="mt-4 space-y-2 rounded-xl bg-white/[.04] p-4 text-sm"><div className="flex justify-between"><dt>Client service fee (10%)</dt><dd>LKR {(Number(quoteAmount) * .1).toFixed(2)}</dd></div><div className="flex justify-between font-semibold"><dt>Client total</dt><dd>LKR {(Number(quoteAmount) * 1.1).toFixed(2)}</dd></div><div className="flex justify-between"><dt>Editor commission (10%)</dt><dd>LKR {(Number(quoteAmount) * .1).toFixed(2)}</dd></div><div className="flex justify-between font-semibold text-emerald-200"><dt>Editor net earning</dt><dd>LKR {(Number(quoteAmount) * .9).toFixed(2)}</dd></div></dl>}{quoteError && <p role="alert" className="mt-4 rounded-lg bg-red-400/10 p-3 text-sm text-red-200">{quoteError}</p>}<p className="mt-3 text-xs text-slate-500">The secure payment request expires seven days after the backend saves it.</p><div className="mt-6 flex gap-3"><button type="button" onClick={() => { setQuoteOpen(false); setQuoteError(""); }} className="flex-1 rounded-xl border border-white/15 px-4 py-3">Cancel</button><button type="submit" disabled={quoteSubmitting || !(Number(quoteAmount) >= 100)} className="flex-1 rounded-xl bg-brand-gradient px-4 py-3 font-semibold text-black disabled:opacity-50">{quoteSubmitting ? "Sending…" : "Send to Client"}</button></div></form></div>}
      {isProjectOwner && requestAccepted && !quote && <section className="mx-3 mb-2 rounded-xl border border-white/10 bg-white/[.035] px-4 py-3 text-sm text-slate-300">Waiting for the editor to set the final project price.</section>}
      {isEditor && requestAccepted && !quote && <section className="mx-3 mb-2 max-w-3xl rounded-2xl border border-white/10 bg-[#0d0d0d]/85 p-6"><p className="text-slate-300">Set the final project amount to request payment.</p>{canSetFinalAmount && <button type="button" onClick={() => setQuoteOpen(true)} className="chat-action mt-4 rounded-xl px-5 py-3 font-semibold">Set Final Project Amount</button>}</section>}
      {quoteSuccess && <section role="status" className="mx-3 mb-2 rounded-xl border border-emerald-300/25 bg-emerald-400/10 px-4 py-3 text-sm text-emerald-100">{quoteSuccess}</section>}
      {projectInfoOpen && request.brief && <section className="mx-3 mb-2 rounded-2xl border border-white/10 bg-[#0d0d0d]/90 p-4 text-xs"><strong className="text-white">Detailed brief</strong><div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-slate-400"><span>{request.brief.content_type}</span><span>{request.brief.aspect_ratio}</span><span>{request.brief.output_format || "Format flexible"}</span><span>Source: {request.brief.source_duration_minutes || "—"} min</span><span>Target: {request.brief.target_duration_minutes || "—"} min</span><span>{request.brief.requested_revision_limit} revisions requested</span>{request.brief.budget_min != null && <span>Budget Rs. {Number(request.brief.budget_min).toLocaleString()}–{Number(request.brief.budget_max || request.brief.budget_min).toLocaleString()}</span>}</div></section>}
      {projectInfoOpen && ["accepted", "payment_failed"].includes(request.status) && <section className="mx-3 mb-2 rounded-2xl border border-brand-gold/20 bg-brand-gold/5 p-4 text-xs"><div className="flex flex-wrap items-center gap-3"><strong className="text-brand-gold">Proposal & negotiation</strong>{request.proposal_version ? <><span>Rs. {Number(request.proposal_amount).toLocaleString("en-LK")}</span><span>{request.proposal_delivery_days} days</span><span>{request.proposal_revision_limit} revisions</span><span className="rounded-full bg-white/10 px-2 py-1">{request.proposal_status?.replaceAll("_", " ")}</span></> : <span className="text-slate-400">No offer submitted</span>}{isEditor && request.proposal_status !== "awaiting_client" && <button type="button" onClick={() => negotiate(false)} className="rounded-lg bg-brand-gold/15 px-3 py-2 text-brand-gold">Make proposal</button>}{!isEditor && request.proposal_version && request.proposal_status === "awaiting_client" && <><button type="button" onClick={acceptProposal} className="rounded-lg bg-emerald-400/15 px-3 py-2 text-emerald-200">Accept proposal</button><button type="button" onClick={() => negotiate(true)} className="rounded-lg bg-white/10 px-3 py-2">Counter offer</button></>}{isEditor && request.proposal_status === "awaiting_editor" && <button type="button" onClick={acceptProposal} className="rounded-lg bg-emerald-400/15 px-3 py-2 text-emerald-200">Accept counter</button>}</div>{request.proposal_message && <p className="mt-2 text-slate-400">{request.proposal_message}</p>}</section>}
      {securityOpen && <section className="mx-3 mb-2 rounded-2xl border border-brand-gold/20 bg-[#0d0d0d]/95 p-4 text-xs shadow-xl">
        <div className="flex flex-wrap items-center gap-3"><strong className="text-brand-goldLight">Safe media access</strong><span>Links expire in 5 minutes</span><span>Watermarked preview</span><span>Delete {security?.retention_days || 30} days after project</span>
          {!isEditor && <button type="button" onClick={toggleDownloads} className="rounded-lg bg-white/10 px-3 py-2">Editor downloads: {security?.editor_download_allowed ? "Allowed" : "Blocked"}</button>}
          {!isEditor && request.status === "delivered" && <button type="button" onClick={() => lifecycleReason("revision")} className="rounded-lg bg-amber-400/10 px-3 py-2 text-amber-200">Request revision</button>}
          {isEditor && request.status === "revision_requested" && <button type="button" onClick={acceptRevision} className="rounded-lg bg-brand-gold/10 px-3 py-2 text-brand-goldLight">Accept revision</button>}
          {request.status === "cancel_requested" ? request.cancel_requested_by !== user?.id && <><button type="button" onClick={() => decideCancellation(true)} className="rounded-lg bg-red-400/10 px-3 py-2 text-red-200">Accept cancellation</button><button type="button" onClick={() => decideCancellation(false)} className="rounded-lg bg-white/10 px-3 py-2">Decline cancellation</button></> : !chatClosed && <button type="button" onClick={() => lifecycleReason("cancel")} className="rounded-lg bg-red-400/10 px-3 py-2 text-red-200">Request cancellation</button>}
          {["in_progress", "overdue", "admin_review", "revision_requested", "delivered", "cancel_requested", "completed"].includes(request.status) && <button type="button" onClick={() => lifecycleReason("dispute")} className="rounded-lg bg-red-400/10 px-3 py-2 text-red-200">Open dispute</button>}
          {!isEditor && request.status === "refund_pending" && <button type="button" onClick={processRefund} className="rounded-lg bg-emerald-400/10 px-3 py-2 text-emerald-200">Process refund</button>}
          <button type="button" onClick={reportMisuse} className="rounded-lg bg-red-400/10 px-3 py-2 text-red-200">Report file misuse</button>
          <button type="button" onClick={reportConversation} className="rounded-lg bg-red-400/10 px-3 py-2 text-red-200">Report conversation</button>
        </div>
      </section>}
      {agreementBlocked && <section className="mx-3 mb-2 rounded-2xl border border-amber-300/25 bg-amber-400/10 p-4 text-sm text-amber-100"><p>Before accessing client files, you agree to use them only for this project, not redistribute them, and respect the client’s download permission and retention policy.</p><button type="button" onClick={acceptAgreement} className="mt-3 rounded-lg bg-amber-300 px-4 py-2 font-semibold text-slate-950">Accept editor agreement</button></section>}

      {delivery && <section className="mx-3 mb-2 rounded-2xl border border-brand-gold/25 bg-[#0d0d0d]/95 p-4 shadow-xl">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-[.16em] text-brand-gold">Final Delivery</p>
            <h2 className="mt-1 truncate font-semibold text-white">{delivery.original_filename}</h2>
            <p className="mt-1 text-xs text-[#a7a7a7]">{(Number(delivery.file_size || 0) / 1048576).toFixed(2)} MB · Order {delivery.order_id || requestId}</p>
            <p className="mt-2 text-sm text-[#bdbdbd]">{delivery.delivery_status === "RELEASED" ? "Payment verified — final output unlocked" : delivery.delivery_status === "PAYMENT_PENDING" ? "Waiting for PayHere confirmation" : isEditor ? "Final output validated. Waiting for client payment." : "Your final edited video is ready. Complete payment to unlock it."}</p>
          </div>
          <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${delivery.delivery_status === "RELEASED" ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300" : "border-amber-300/30 bg-amber-300/10 text-amber-200"}`}>{delivery.delivery_status.replaceAll("_", " ")}</span>
        </div>
        {delivery.can_access && <div className="mt-4 flex flex-wrap gap-2"><button type="button" onClick={() => openFinalDelivery("preview")} className="rounded-lg border border-brand-gold/40 bg-brand-gold/10 px-4 py-2 text-sm font-semibold text-brand-goldLight"><Eye className="mr-2 inline" size={16} />Secure View</button>{delivery.delivery_status === "RELEASED" && <button type="button" onClick={() => openFinalDelivery("download")} className="rounded-lg bg-gradient-to-br from-white via-[#d8d8d8] to-[#afafaf] px-4 py-2 text-sm font-semibold text-black"><Download className="mr-2 inline" size={16} />Secure Download</button>}</div>}
      </section>}

      </div>
      <div className={uploadOpen ? "workspace-scroll flex-1 overflow-y-auto p-3 sm:p-6" : "hidden"}>
        <header><p className="text-xs font-bold uppercase tracking-[.18em] text-brand-gold">Upload & Share</p><h1 className="mt-1 text-2xl font-bold">Share project resources</h1><p className="mt-1 text-sm text-slate-400">Share project resources safely with your editor or client.</p></header>
        <div className="mt-6 grid gap-5 xl:grid-cols-2">
          <section className="workspace-card"><span className="workspace-card-icon"><Image size={25} /></span><h2 className="mt-5 text-xl font-semibold">Upload Image</h2><p className="mt-2 text-sm text-slate-400">Share reference images, screenshots or design assets.</p><button type="button" onClick={() => imageInputRef.current?.click()} disabled={uploading || chatClosed} className="mt-6 flex min-h-36 w-full flex-col items-center justify-center rounded-2xl border border-dashed border-brand-gold/30 bg-black/25 p-5 text-center hover:border-brand-gold/60 disabled:opacity-50"><UploadCloud className="text-brand-gold" /><span className="mt-3 font-semibold">Choose Image</span><span className="mt-1 text-xs text-slate-500">PNG, JPG, JPEG or WEBP</span></button>
          {selectedFile && fileCategory(selectedFile) === "image" && <div className="mt-4 rounded-xl bg-white/[.04] p-3">{previewUrl && <img src={previewUrl} alt="Selected image preview" className="mb-3 h-40 w-full rounded-lg object-contain" />}<p className="truncate text-sm font-semibold">{selectedFile.name}</p><p className="text-xs text-slate-400">{(selectedFile.size / 1048576).toFixed(2)} MB</p>{(uploadStage === "uploading" || uploadStage === "processing") && <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-brand-gold" style={{ width: `${uploadStage === "processing" ? 100 : uploadProgress}%` }} /></div>}<div className="mt-4 flex gap-2"><button type="button" onClick={clearSelectedFile} disabled={uploading} className="rounded-lg border border-white/10 px-4 py-2 text-sm">Cancel</button><button type="button" onClick={sendSelectedFile} disabled={uploading || !connected || !joined} className="chat-action flex-1 rounded-lg px-4 py-2 text-sm font-semibold">{uploading ? uploadStage === "processing" ? "Processing…" : `Uploading… ${uploadProgress}%` : uploadStage === "failed" ? "Retry Upload" : "Upload Image"}</button></div></div>}
          </section>
          <section className="workspace-card"><span className="workspace-card-icon"><Link2 size={25} /></span><h2 className="mt-5 text-xl font-semibold">Google Drive Link</h2><p className="mt-2 text-sm text-slate-400">Share large project files using a Google Drive link.</p><form onSubmit={sendDriveLink} className="mt-6"><label className="text-sm font-semibold" htmlFor="drive-link">Paste Google Drive link</label><div className="mt-2 flex items-center gap-2 rounded-xl border border-white/10 bg-black/30 px-4 focus-within:border-brand-gold/50"><Link2 size={18} className="text-slate-500" /><input id="drive-link" type="url" placeholder="https://drive.google.com/..." value={driveLink} onChange={(event) => { setDriveLink(event.target.value); setDriveError(""); setDriveSuccess(""); }} className="min-w-0 flex-1 bg-transparent py-4 text-sm outline-none" /></div>{driveError && <p role="alert" className="mt-3 text-sm text-red-300">{driveError}</p>}{driveSuccess && <p role="status" className="mt-3 text-sm text-emerald-300">{driveSuccess}</p>}<button type="submit" disabled={!driveLink.trim() || driveSending || !joined || chatClosed} className="chat-action mt-5 flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 font-semibold disabled:opacity-50">{driveSending ? "Sending…" : <><Send size={17} />Send Drive Link</>}</button></form></section>
        </div>
        <input ref={imageInputRef} type="file" hidden accept="image/jpeg,image/png,image/webp" onChange={handleFile} />
      </div>
      <div className={chatOpen ? "contents" : "hidden"}>

      <main className="whatsapp-wallpaper flex-1 overflow-y-auto px-3 py-5 sm:px-8">
        <div className="mx-auto max-w-4xl space-y-1.5">
          <div className="liquid-glass mx-auto mb-5 w-fit rounded-full px-4 py-2 text-[10px] font-medium uppercase tracking-[.14em] text-slate-400 shadow">Messages are stored securely</div>
          {hasOlderMessages && <div className="flex justify-center"><button type="button" onClick={loadOlderMessages} disabled={loadingOlder} className="mb-4 rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs text-slate-300 disabled:opacity-50">{loadingOlder ? "Loading…" : "Load older messages"}</button></div>}
          {normalizeMessages(messages).map((message, index) => {
            const mine = message.sender_id === user?.id;
            const messageKey = toDisplayText(messageIdentity(message), `${message.created_at || "message"}-${index}`);
            return (
              <div key={messageKey} className={`chat-message-in flex ${mine ? "justify-end" : "justify-start"}`}>
                <div className={`relative max-w-[86%] rounded-2xl border px-3 pb-2 pt-2.5 shadow-lg backdrop-blur-xl sm:max-w-[68%] ${mine ? "chat-tail-right border-brand-gold/15 bg-gradient-to-br from-[#6b531a]/90 to-[#3a2d12]/90" : "chat-tail-left border-white/10 bg-[#141414]/85"}`}>
                  {message.file_url && <Attachment message={message} watermark={`EDITZONE • ${user?.username || user?.email || "MEMBER"}`} downloadsAllowed={downloadsAllowed} senderOwned={mine} />}
                  {message.timecode_seconds != null && <span className="mb-1 inline-flex rounded-md bg-brand-gold/15 px-2 py-1 text-xs font-bold text-brand-goldLight">▶ {String(Math.floor(message.timecode_seconds / 60)).padStart(2, "0")}:{String(message.timecode_seconds % 60).padStart(2, "0")}</span>}
                  {message.text && /^https:\/\/(?:drive|docs)\.google\.com\//i.test(message.text) ? <div className="min-w-[220px] rounded-xl border border-white/10 bg-black/20 p-3"><div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl bg-brand-gold/15 text-brand-gold"><Link2 size={20} /></span><div><p className="font-semibold">Google Drive</p><p className="text-xs text-slate-400">Project files · {new URL(message.text).hostname}</p></div></div><a href={message.text} target="_blank" rel="noopener noreferrer" className="mt-3 flex items-center justify-center gap-2 rounded-lg border border-brand-gold/25 bg-brand-gold/10 px-3 py-2 text-sm font-semibold text-brand-goldLight">Open Drive <ExternalLink size={14} /></a></div> : message.text && <p className="whitespace-pre-wrap break-words pr-12 text-[14.5px] leading-[1.35] text-[#e9edef]">{message.text}</p>}
                  <span className="ml-8 flex translate-y-0.5 items-center justify-end gap-1 text-[10px] text-[#a7a7a7]">
                    {formatTime(message.created_at)} {mine && message.delivery_status === "pending" ? <span className="text-slate-400">sending…</span> : mine && message.delivery_status === "failed" ? <button type="button" onClick={() => retryFailedMessage(message.client_message_id)} className="font-semibold text-red-300 underline">Retry</button> : mine && <CheckCheck size={15} className={message.read_at ? "text-[#d4af37]" : "text-slate-400"} />}
                  </span>
                </div>
              </div>
            );
          })}
          {otherTyping && <div className="chat-message-in w-fit rounded-2xl border border-white/10 bg-[#141414]/85 px-4 py-3 shadow backdrop-blur-xl"><span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" /></div>}
          <div ref={bottomRef} />
        </div>
      </main>

      {visibleError && <div className="flex items-center justify-center gap-3 bg-red-950/90 px-4 py-2 text-center text-xs text-red-200"><span>{visibleError}</span>{!connected && connectionState === "unavailable" && <button type="button" onClick={retryConnection} className="rounded-full border border-red-200/30 px-3 py-1 font-semibold hover:bg-white/10">Retry</button>}</div>}
      {selectedFile && !chatClosed && <section className="mx-2 mb-2 rounded-2xl border border-brand-gold/20 bg-[#0d0d0d]/95 p-3 sm:mx-3">
        <div className="flex items-center gap-3">
          {previewUrl && selectedFile.type.startsWith("image/") ? <img src={previewUrl} alt="Selected attachment preview" className="h-24 w-36 rounded-lg bg-black object-contain" /> : previewUrl && selectedFile.type.startsWith("audio/") ? <audio src={previewUrl} controls className="h-10 w-36" /> : previewUrl ? <video src={previewUrl} controls preload="metadata" className="h-24 w-36 rounded-lg bg-black object-contain" /> : <div className="grid h-16 w-16 place-items-center rounded-lg bg-white/10"><FileText /></div>}
          <div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{selectedFile.name}</p><p className="text-xs text-slate-400">{(selectedFile.size / 1048576).toFixed(2)} MB of {FILE_LIMIT_MB[fileCategory(selectedFile, { viewOnce })]} MB · {uploadStage === "processing" ? "Security scan…" : uploadStage === "failed" ? "Upload failed — retry available" : uploadStage === "sent" ? "Upload successful" : uploadStage}</p>
            <label className="mt-2 flex items-center gap-2 text-xs text-brand-gold"><input type="checkbox" checked={viewOnce} onChange={(event) => setViewOnce(event.target.checked)} disabled={uploading} /> View once</label>
            {(uploadStage === "uploading" || uploadStage === "processing") && <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full bg-gradient-to-r from-brand-goldDeep to-brand-gold transition-all" style={{ width: `${uploadStage === "processing" ? 100 : uploadProgress}%` }} /></div>}
          </div>
          <button type="button" onClick={clearSelectedFile} disabled={uploading} className="rounded-lg px-3 py-2 text-xs text-slate-300 hover:bg-white/10">Cancel</button>
          <button type="button" onClick={sendSelectedFile} disabled={uploading || !connected || !joined} className="chat-action rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-40">{uploadStage === "failed" ? "Retry" : "Send"}</button>
        </div>
      </section>}
      {voicePreview && !chatClosed && <section className="mx-3 mb-2 flex items-center gap-3 rounded-2xl border border-brand-gold/20 bg-[#0d0d0d]/95 p-3"><audio src={voicePreview.url} controls className="h-10 flex-1" aria-label="Voice message preview" /><span className="text-xs text-slate-400">{voicePreview.duration}s · {(voicePreview.blob.size / 1048576).toFixed(2)} MB / {FILE_LIMIT_MB.voice} MB · {voiceStage === "processing" ? "Waiting for security scan…" : voiceStage === "failed" ? "Not sent — retry available" : voiceStage === "uploading" ? `Uploading ${uploadProgress}%` : connected && joined ? "Ready" : "Ready — reconnecting before send"}</span><button type="button" onClick={discardVoicePreview} disabled={uploading} aria-label="Discard voice recording" className="rounded-full p-2 text-red-200 disabled:opacity-40"><Trash2 size={18} /></button><button type="button" onClick={sendVoicePreview} disabled={uploading || agreementBlocked} className="chat-action rounded-full px-4 py-2 text-sm font-semibold disabled:opacity-40">{voiceStage === "failed" ? "Retry" : uploading ? voiceStage === "processing" ? "Processing…" : `${uploadProgress}%` : "Send"}</button></section>}
      <input ref={imageInputRef} type="file" hidden accept="image/jpeg,image/png,image/webp" onChange={handleFile} />
      <input ref={videoInputRef} type="file" hidden accept="video/mp4,video/webm,video/quicktime,.mov" onChange={handleFile} />
      <input ref={audioInputRef} type="file" hidden accept="audio/mpeg,audio/wav,audio/mp4,audio/ogg,audio/webm,.mp3,.wav,.m4a,.ogg,.webm" onChange={handleFile} />
      <input ref={documentInputRef} type="file" hidden accept=".pdf,.doc,.docx,.txt,.zip" onChange={handleFile} />
      {attachmentMenuOpen && !chatClosed && <section className="mx-3 mb-2 grid grid-cols-2 gap-2 rounded-2xl border border-white/10 bg-[#0d0d0d]/95 p-3 text-sm shadow-xl sm:grid-cols-4">{[
        ["Images", imageInputRef], ["Videos & reels", videoInputRef],
        ["Audio & songs", audioInputRef], ["Documents & ZIP", documentInputRef],
      ].map(([label, inputRef]) => <button type="button" key={label} onClick={() => { setAttachmentMenuOpen(false); inputRef.current?.click(); }} disabled={uploading || !connected || !joined || agreementBlocked} className="cursor-pointer rounded-xl bg-white/5 p-3 text-center text-slate-200 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40">{label}</button>)}</section>}
      {!chatClosed && !isEditor && ["in_progress", "overdue", "admin_review", "delivered", "revision_requested"].includes(request.status) && <button type="button" onClick={timestampFeedback} className="mx-auto mb-2 rounded-full border border-brand-gold/20 bg-brand-gold/10 px-4 py-2 text-xs font-semibold text-brand-goldLight">＋ Timestamp video feedback</button>}
      {chatClosed ? (
        <div className="liquid-chat-bar m-2 mt-0 flex min-h-[66px] items-center justify-center gap-3 rounded-2xl border border-emerald-300/20 px-4 py-3 text-center text-sm text-emerald-200 sm:m-3 sm:mt-0">
          <CircleCheck size={20} />
          <span>Payment completed successfully. This conversation is now closed.</span>
        </div>
      ) : <form onSubmit={sendText} className="liquid-chat-bar m-2 mt-0 flex min-h-[66px] items-center gap-2 rounded-2xl px-3 py-2.5 sm:m-3 sm:mt-0 sm:px-5">
        <button type="button" onClick={() => setAttachmentMenuOpen((value) => !value)} aria-label="Open attachment menu" className={`mb-2 cursor-pointer rounded-full p-1 text-[#a7a7a7] hover:text-white ${uploading ? "animate-pulse" : ""}`}>
          <Paperclip size={23} />
        </button>
        {recording ? (
          <div className="flex min-h-11 flex-1 items-center gap-3 rounded-2xl border border-red-300/20 bg-red-400/10 px-4 text-sm text-red-100">
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-400" />
            <span className="font-semibold">Recording</span>
            <button type="button" onClick={toggleRecordingPause} aria-label={recordingPaused ? "Resume recording" : "Pause recording"} className="rounded-full p-2 hover:bg-white/10">{recordingPaused ? <Play size={16} /> : <Pause size={16} />}</button>
            <button type="button" onClick={cancelRecording} aria-label="Cancel recording" className="rounded-full p-2 hover:bg-white/10"><Trash2 size={16} /></button>
            <span className="ml-auto text-xs text-red-200/70">{recordingPaused ? "paused" : "max 5 min"}</span>
            <span className="tabular-nums">{String(Math.floor(recordingSeconds / 60)).padStart(2, "0")}:{String(recordingSeconds % 60).padStart(2, "0")}</span>
          </div>
        ) : <textarea value={text} maxLength={MAX_TEXT_MESSAGE_LENGTH} onChange={(event) => updateText(event.target.value)} onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendText(event); }
        }} rows={1} placeholder={joined ? "Type a message" : "Waiting for chat connection…"} disabled={!connected || !joined} className="max-h-32 min-h-11 flex-1 resize-none rounded-2xl border border-white/[0.08] bg-white/[0.06] px-4 py-3 text-sm text-white outline-none placeholder:text-slate-500 focus:border-brand-gold/30 focus:bg-white/[0.09]" />}
        {text.trim() && !recording ? (
          <button type="submit" disabled={!connected || !joined || sending} aria-label={sending ? "Sending message" : "Send message"} className="chat-action grid h-11 w-11 shrink-0 place-items-center rounded-full text-white transition hover:scale-105 disabled:cursor-not-allowed disabled:opacity-40">{sending ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" /> : <Send size={19} />}</button>
        ) : (
          <button type="button" onClick={recording ? stopRecording : startRecording} disabled={uploading || agreementBlocked} aria-label={recording ? "Stop voice recording" : "Record voice message"} className={`grid h-11 w-11 shrink-0 place-items-center rounded-full text-white transition hover:scale-105 disabled:cursor-not-allowed disabled:opacity-40 ${recording ? "bg-red-500 shadow-lg shadow-red-500/25" : "chat-action"}`}>
            {recording ? <Square size={16} fill="currentColor" /> : <Mic size={19} />}
          </button>
        )}
      </form>}
      {profileOpen && <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onClick={() => setProfileOpen(false)}><aside className="h-full w-full max-w-sm overflow-y-auto border-l border-white/10 bg-[#0d0d0d] p-6 shadow-2xl" onClick={(event) => event.stopPropagation()}><button type="button" onClick={() => setProfileOpen(false)} className="float-right rounded-full p-2 hover:bg-white/10">×</button>{participant ? <div className="mt-10"><div className="mx-auto grid h-24 w-24 place-items-center rounded-full bg-avatar-gradient text-2xl font-bold text-brand-goldWarm">{participant.display_name?.slice(0, 2).toUpperCase()}</div><h2 className="mt-4 text-center text-xl font-semibold">{participant.display_name}</h2><p className="text-center text-sm capitalize text-brand-goldLight">{participant.role} {participant.verified ? "· verified" : ""}</p>{participant.category && <p className="mt-6 text-sm text-slate-300">{participant.category}</p>}{participant.bio && <p className="mt-3 text-sm leading-6 text-slate-400">{participant.bio}</p>}{participant.skills?.length > 0 && <div className="mt-4 flex flex-wrap gap-2">{participant.skills.map((skill) => <span key={skill} className="rounded-full bg-white/5 px-3 py-1 text-xs">{skill}</span>)}</div>}<div className="mt-6 rounded-xl bg-white/5 p-4 text-sm text-slate-400"><p>Project: {participant.project_title}</p><p className="mt-1 capitalize">Status: {participant.project_status?.replaceAll("_", " ")}</p>{participant.rating_avg != null && <p className="mt-1">Rating: {participant.rating_avg} ({participant.rating_count || 0} reviews)</p>}</div></div> : <Loader label="Loading profile…" />}</aside></div>}

      </div>
      </div>
      </div>
    </div>
  );
}
