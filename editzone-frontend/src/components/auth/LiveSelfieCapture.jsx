import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, CheckCircle2, RefreshCw, ShieldCheck, Upload } from "lucide-react";
import { FaceLandmarker, FilesetResolver } from "@mediapipe/tasks-vision";
import api from "../../services/api";
import { ErrorText, PrimaryButton } from "../common/UI";

const MAX_OUTPUT_WIDTH = 960;
const JPEG_QUALITY = 0.82;
// A natural blink is often shorter than 250 ms. Sampling faster prevents users
// from having to hold both eyes closed just to satisfy the liveness challenge.
const DETECTION_INTERVAL_MS = 100;
const BLINK_CLOSED_THRESHOLD = 0.38;
const BLINK_OPEN_THRESHOLD = 0.22;
const VISION_WASM_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22-rc.20250304/wasm";
const FACE_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task";

function cameraErrorMessage(error) {
  const messages = {
    NotAllowedError: "Camera permission denied. Allow camera access in browser settings, then try again.",
    NotFoundError: "No camera was detected on this device.",
    NotReadableError: "The camera is already in use by another application.",
    OverconstrainedError: "The camera does not support the requested settings.",
    AbortError: "Camera startup was interrupted. Please try again.",
    SecurityError: "Camera access is blocked because this page is not running on localhost or HTTPS.",
  };
  return messages[error?.name] || apiError(error, "The camera could not be started");
}

function apiError(error, fallback) {
  const codeMessages = {
    NO_FACE_DETECTED: "No face detected. Look directly at the camera and try again.",
    MULTIPLE_FACES: "Only one person should be visible.",
    LOW_IMAGE_QUALITY: "Your face is not clear enough. Use better lighting and hold the camera steady.",
    FACE_NOT_MATCHED: "Your selfie could not be matched with your verified identity. Please try again.",
    INVALID_SELFIE_IMAGE: "The captured image could not be read. Capture a clear JPG selfie.",
    INVALID_REFERENCE_IMAGE: "Your verified NIC image is unavailable. Upload the NIC image again.",
    AWS_ACCESS_DENIED: "Face verification is not configured correctly. Please contact support.",
    FACE_VERIFICATION_NOT_CONFIGURED: "Face verification is not configured correctly. Please contact support.",
    AWS_CREDENTIALS_INVALID: "Face verification credentials are invalid or expired. Please contact support.",
    AWS_UNAVAILABLE: "Face verification service is temporarily unavailable. Please try again.",
  };
  const errorCode = error.response?.data?.code;
  if (errorCode && codeMessages[errorCode]) return codeMessages[errorCode];
  return error.response?.data?.message
    || error.response?.data?.detail
    || error.message
    || fallback;
}

async function compressFrame(video) {
  const scale = Math.min(1, MAX_OUTPUT_WIDTH / video.videoWidth);
  const width = Math.max(1, Math.round(video.videoWidth * scale));
  const height = Math.max(1, Math.round(video.videoHeight * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { alpha: false });
  context.translate(width, 0);
  context.scale(-1, 1);
  context.drawImage(video, 0, 0, width, height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
  if (!blob) throw new Error("The selfie could not be captured. Please try again");
  return { blob, previewUrl: URL.createObjectURL(blob) };
}

const CHALLENGE_LABELS = {
  blink: "Blink once naturally",
  turn_left: "Slowly turn your head left",
  turn_right: "Slowly turn your head right",
};

export default function LiveSelfieCapture({ onVerified, onStatus }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const detectorRef = useRef(null);
  const detectionTimerRef = useRef(null);
  const mountedRef = useRef(true);
  const capturedRef = useRef(false);
  const startAttemptRef = useRef(0);
  const challengeRef = useRef([]);
  const challengeIndexRef = useRef(0);
  const actionStreakRef = useRef(0);
  const blinkReadyRef = useRef(false);
  const bestActionConfidenceRef = useRef(0);
  const uploadLockRef = useRef(false);

  const [cameraState, setCameraState] = useState("starting");
  const [faceCount, setFaceCount] = useState(0);
  const [captureSession, setCaptureSession] = useState(null);
  const [captured, setCaptured] = useState(null);
  const [capturedAt, setCapturedAt] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState("");
  const [challengeIndex, setChallengeIndex] = useState(0);
  const [livenessEvents, setLivenessEvents] = useState([]);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const stopCamera = useCallback(() => {
    if (detectionTimerRef.current) window.clearTimeout(detectionTimerRef.current);
    detectionTimerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  const detectFaces = useCallback(async () => {
    if (
      !mountedRef.current
      || !detectorRef.current
      || !videoRef.current
      || videoRef.current.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
      || capturedRef.current
    ) {
      return;
    }
    try {
      const result = detectorRef.current.detectForVideo(videoRef.current, performance.now());
      const count = result.faceLandmarks.length;
      if (mountedRef.current) {
        setFaceCount(count);
        const action = challengeRef.current[challengeIndexRef.current];
        if (count === 1 && action) {
          const scores = Object.fromEntries(
            (result.faceBlendshapes?.[0]?.categories || []).map(
              (category) => [category.categoryName, category.score],
            ),
          );
          const landmarks = result.faceLandmarks[0];
          const leftEdge = landmarks[234]?.x;
          const rightEdge = landmarks[454]?.x;
          const nose = landmarks[1]?.x;
          const ratio = Number.isFinite(leftEdge) && Number.isFinite(rightEdge) && rightEdge !== leftEdge
            ? (nose - leftEdge) / (rightEdge - leftEdge)
            : 0.5;
          const leftBlink = scores.eyeBlinkLeft || 0;
          const rightBlink = scores.eyeBlinkRight || 0;
          const blinkScore = Math.min(leftBlink, rightBlink);
          const confidence = action === "blink"
            ? blinkScore
            : action === "turn_left"
              ? Math.max(0, (ratio - 0.5) * 6)
              : Math.max(0, (0.5 - ratio) * 6);
          let actionComplete = false;
          if (action === "blink") {
            // Require an eyes-open frame before accepting an eyes-closed frame.
            // This detects a real blink while avoiding both false positives and
            // the old requirement to keep both eyes closed for ~750 ms.
            if (leftBlink <= BLINK_OPEN_THRESHOLD && rightBlink <= BLINK_OPEN_THRESHOLD) {
              blinkReadyRef.current = true;
            }
            actionComplete = blinkReadyRef.current && blinkScore >= BLINK_CLOSED_THRESHOLD;
          } else {
            actionStreakRef.current = confidence >= 0.55 ? actionStreakRef.current + 1 : 0;
            bestActionConfidenceRef.current = Math.max(bestActionConfidenceRef.current, confidence);
            actionComplete = actionStreakRef.current >= 2;
          }
          if (actionComplete) {
            const event = {
              action,
              // The server's confidence floor represents successful client-side
              // evidence. Keep the raw detector threshold device-friendly.
              confidence: Math.min(1, Math.max(0.65, confidence, bestActionConfidenceRef.current)),
              completed_at: new Date().toISOString(),
            };
            setLivenessEvents((current) => [...current, event]);
            challengeIndexRef.current += 1;
            setChallengeIndex(challengeIndexRef.current);
            actionStreakRef.current = 0;
            blinkReadyRef.current = false;
            bestActionConfidenceRef.current = 0;
          }
        } else {
          actionStreakRef.current = 0;
        }
      }
    } catch {
      if (mountedRef.current) setError("Face detection paused. Keep the camera open and try again");
    } finally {
      if (mountedRef.current && !capturedRef.current) {
        detectionTimerRef.current = window.setTimeout(detectFaces, DETECTION_INTERVAL_MS);
      }
    }
  }, []);

  const startCamera = useCallback(async () => {
    const attempt = ++startAttemptRef.current;
    setError("");
    setCameraState("starting");
    setCaptureSession(null);
    challengeRef.current = [];
    setCaptured(null);
    capturedRef.current = false;
    setPreviewUrl("");
    setFaceCount(0);
    setChallengeIndex(0);
    challengeIndexRef.current = 0;
    actionStreakRef.current = 0;
    blinkReadyRef.current = false;
    bestActionConfidenceRef.current = 0;
    setLivenessEvents([]);
    stopCamera();
    try {
      if (!window.isSecureContext && window.location.hostname !== "localhost") {
        throw new Error("Camera access requires HTTPS");
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("This browser does not support camera access");
      }

      const vision = await FilesetResolver.forVisionTasks(VISION_WASM_URL);
      const detector = await FaceLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: FACE_MODEL_URL, delegate: "CPU" },
        runningMode: "VIDEO",
        numFaces: 2,
        minFaceDetectionConfidence: 0.7,
        minFacePresenceConfidence: 0.7,
        minTrackingConfidence: 0.7,
        outputFaceBlendshapes: true,
      });
      if (!mountedRef.current || attempt !== startAttemptRef.current) {
        detector.close();
        return;
      }
      detectorRef.current?.close();
      detectorRef.current = detector;

      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "user" }, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
      } catch (preferredError) {
        if (["NotAllowedError", "SecurityError", "NotFoundError", "NotReadableError"].includes(preferredError.name)) throw preferredError;
        stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      }
      if (!mountedRef.current || attempt !== startAttemptRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      videoRef.current.srcObject = stream;
      await videoRef.current.play();

      // Start the short-lived backend session only when the camera is visibly
      // playing. Permission prompts and slow camera startup can otherwise use up
      // the whole session before the user ever sees the liveness instruction.
      const sessionResponse = await api.post("/editor/selfie/session");
      if (!mountedRef.current || attempt !== startAttemptRef.current) return;
      setCaptureSession(sessionResponse.data);
      challengeRef.current = sessionResponse.data.challenge || [];
      setCameraState("ready");
      detectionTimerRef.current = window.setTimeout(detectFaces, DETECTION_INTERVAL_MS);
    } catch (startError) {
      if (!mountedRef.current || attempt !== startAttemptRef.current) return;
      stopCamera();
      setCameraState("error");
      setError(cameraErrorMessage(startError));
    }
  }, [detectFaces, stopCamera]);

  useEffect(() => {
    mountedRef.current = true;
    api.get("/editor/identity/status")
      .then(({ data }) => {
        if (!mountedRef.current) return;
        if (data.registration_allowed) {
          setVerified(true);
          setCameraState("verified");
          onVerified(true);
        } else if (data.nic_front_verified) {
          startCamera();
        } else {
          setCameraState("blocked");
          setError("Verify your NIC first. The camera remains disabled until the backend confirms NIC verification.");
        }
      })
      .catch((statusError) => {
        setCameraState("error");
        setError(apiError(statusError, "The latest NIC verification status could not be loaded"));
      });
    return () => {
      mountedRef.current = false;
      startAttemptRef.current += 1;
      stopCamera();
      detectorRef.current?.close();
      detectorRef.current = null;
    };
  }, [onStatus, onVerified, startCamera, stopCamera]);

  const captureSelfie = async () => {
    setError("");
    if (!videoRef.current || faceCount !== 1) {
      setError(faceCount === 0
        ? "No face detected. Center your face in the frame"
        : "Multiple faces detected. Only one person may be in the frame");
      return;
    }
    try {
      if (challengeIndexRef.current < challengeRef.current.length) {
        setError("Complete every liveness instruction before capturing the selfie");
        return;
      }
      const latestDetection = detectorRef.current.detectForVideo(
        videoRef.current,
        performance.now(),
      );
      if (latestDetection.faceLandmarks.length !== 1) {
        setFaceCount(latestDetection.faceLandmarks.length);
        setError(latestDetection.faceLandmarks.length === 0
          ? "No face detected. Center your face and try again"
          : "Multiple faces detected. Only one person may be in the frame");
        return;
      }
      const frame = await compressFrame(videoRef.current);
      if (!captureSession) {
        throw new Error("The camera session is not ready. Please start the camera again");
      }
      if (frame.blob.size > captureSession.max_file_size_bytes) {
        throw new Error("The optimized selfie is still too large. Please try again");
      }
      setCaptured(frame.blob);
      capturedRef.current = true;
      setPreviewUrl(frame.previewUrl);
      setCapturedAt(new Date().toISOString());
      if (detectionTimerRef.current) window.clearTimeout(detectionTimerRef.current);
    } catch (captureError) {
      setError(captureError.message || "The selfie could not be captured");
    }
  };

  const retake = () => {
    setCaptured(null);
    capturedRef.current = false;
    setPreviewUrl("");
    setCapturedAt("");
    setFaceCount(0);
    setError("");
    detectionTimerRef.current = window.setTimeout(detectFaces, DETECTION_INTERVAL_MS);
  };

  const confirmAndUpload = async () => {
    // State updates are asynchronous, so use a ref as an immediate lock against
    // a double tap consuming the backend's single-use session twice.
    if (!captured || !captureSession || uploadLockRef.current) return;
    uploadLockRef.current = true;
    setUploading(true);
    setUploadProgress(0);
    setError("");
    const formData = new FormData();
    formData.append("file", captured, `live-selfie-${Date.now()}.jpg`);
    formData.append("session_id", captureSession.session_id);
    formData.append("capture_token", captureSession.capture_token);
    formData.append("captured_at", capturedAt);
    formData.append("face_count", "1");
    formData.append("liveness_events", JSON.stringify(livenessEvents));
    try {
      const { data } = await api.post("/editor/selfie", formData, {
        headers: { "X-Capture-Source": "camera" },
        onUploadProgress: (event) => {
          if (event.total) setUploadProgress(Math.round((event.loaded * 100) / event.total));
        },
      });
      if (!data.selfie_verified || !data.liveness_passed) {
        throw new Error(data.message || (data.identity_status === "failed"
          ? "Your selfie could not be matched with your verified identity. Please try again"
          : "Selfie verification was not completed"));
      }
      setUploadProgress(100);
      stopCamera();
      const refreshed = await api.get("/editor/identity/status");
      onStatus?.(refreshed.data);
      if (refreshed.data.registration_allowed) {
        setVerified(true);
        setCameraState("verified");
        onVerified(true);
      }
    } catch (uploadError) {
      stopCamera();
      setCaptured(null);
      capturedRef.current = false;
      setPreviewUrl("");
      const responseStatus = uploadError.response?.status;
      const responseMessage = apiError(uploadError, "");
      if (responseStatus === 409 && /camera session|expired|already uploading/i.test(responseMessage)) {
        // Session recovery is safe only with a fresh liveness challenge. Reset
        // it automatically instead of leaving the user on a dead error screen.
        try {
          await api.post("/editor/selfie/retry");
          await startCamera();
          return;
        } catch (restartError) {
          setCameraState("error");
          setError(apiError(restartError, "The camera session could not be restarted"));
          return;
        }
      }
      setCameraState("error");
      setError(responseMessage || "The selfie upload failed. Please try again");
    } finally {
      uploadLockRef.current = false;
      setUploading(false);
    }
  };

  const retryCamera = async () => {
    stopCamera();
    try {
      await api.post("/editor/selfie/retry");
    } catch (retryError) {
      setError(apiError(retryError, "The camera session could not be reset"));
      setCameraState("error");
      return;
    }
    startCamera();
  };

  if (verified) {
    return (
      <div className="rounded-xl border border-emerald-400/40 bg-emerald-500/10 p-4 text-sm text-emerald-200">
        <div className="flex items-center gap-2 font-semibold">
          <ShieldCheck size={20} /> Live selfie verified and stored securely
        </div>
      </div>
    );
  }

  const faceMessage = faceCount === 1
    ? challengeIndex < challengeRef.current.length
      ? `Liveness check: ${CHALLENGE_LABELS[challengeRef.current[challengeIndex]]}`
      : "Liveness passed — ready to capture"
    : faceCount > 1
      ? "Multiple faces detected — only one person may be visible"
      : "Center one clearly visible face in the frame";

  return (
    <div className="space-y-4 rounded-xl border border-brand-border bg-brand-panel/60 p-4">
      <div>
        <h2 className="font-semibold text-white">Live Selfie Verification</h2>
        <p className="mt-1 text-xs text-gray-400">
          Use your front camera. Existing image uploads are not accepted.
        </p>
      </div>

      <div className="relative aspect-[4/3] overflow-hidden rounded-xl bg-black">
        <video
          ref={videoRef}
          muted
          playsInline
          aria-label="Live front camera preview"
          className={`h-full w-full scale-x-[-1] object-cover ${captured ? "hidden" : ""}`}
        />
        {previewUrl && (
          <img src={previewUrl} alt="Captured live selfie preview" className="h-full w-full object-cover" />
        )}
        {cameraState === "starting" && (
          <div className="absolute inset-0 grid place-items-center text-sm text-gray-300">
            Starting secure camera…
          </div>
        )}
      </div>

      {!captured && cameraState === "ready" && (
        <p className={`text-sm ${faceCount === 1 ? "text-emerald-300" : "text-amber-300"}`}>
          {faceMessage}
        </p>
      )}
      <ErrorText>{error}</ErrorText>

      {cameraState === "error" && (
        <button type="button" onClick={retryCamera} className="flex items-center gap-2 text-sm text-brand-gold">
          <RefreshCw size={16} /> Try camera again
        </button>
      )}

      {!captured && cameraState === "ready" && (
        <PrimaryButton
          type="button"
          className="w-full"
          onClick={captureSelfie}
          disabled={faceCount !== 1 || challengeIndex < challengeRef.current.length}
        >
          <Camera size={18} /> Capture Live Selfie
        </PrimaryButton>
      )}

      {captured && (
        <div className="grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            onClick={retake}
            disabled={uploading}
            className="flex items-center justify-center gap-2 rounded-lg border border-brand-border px-4 py-2.5 text-sm text-gray-200 disabled:opacity-50"
          >
            <RefreshCw size={17} /> Retake
          </button>
          <PrimaryButton type="button" onClick={confirmAndUpload} disabled={uploading}>
            {uploading ? <Upload size={17} /> : <CheckCircle2 size={17} />}
            {uploading ? `Uploading ${uploadProgress}%` : "Confirm & Upload"}
          </PrimaryButton>
        </div>
      )}

      {uploading && (
        <div className="h-2 overflow-hidden rounded-full bg-black/30" role="progressbar" aria-valuenow={uploadProgress} aria-valuemin="0" aria-valuemax="100">
          <div className="h-full bg-brand-gold transition-all" style={{ width: `${uploadProgress}%` }} />
        </div>
      )}
    </div>
  );
}
