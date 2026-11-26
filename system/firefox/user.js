// >>> MACUBUNTU-PERF-START
// macubuntu Firefox user.js — 8GB RAM performance tuning
// Applied by stages/46-browser-tuning.sh to every Firefox profile root:
//   ~/.mozilla/firefox, ~/snap/firefox/common/.mozilla/firefox,
//   ~/.var/app/org.mozilla.firefox/.mozilla/firefox
// NOTE: MOZ_DISABLE_RDD_SANDBOX is intentionally NOT set here (breaks WebRTC/screenshare + GPU isolation).
// ============================================================================

// --- Memory / process limits (8GB-constrained) ---
user_pref("dom.ipc.processCount", 4);
user_pref("dom.ipc.processCount.prompt", 2);
user_pref("browser.sessionstore.interval", 30000);

// --- Hardware acceleration (VA-API) ---
// Firefox 137+ prefers these over the deprecated media.ffmpeg.vaapi.enabled:
user_pref("media.hardware-video-decoding.enabled", true);
user_pref("media.hardware-video-decoding.force-enabled", true);
// Kept as fallback for older Firefox versions:
user_pref("media.ffmpeg.vaapi.enabled", true);
user_pref("media.ffvp9.enabled", true);
user_pref("media.navigator.video.enabled", true);
user_pref("gfx.webrender.all", true);
user_pref("gfx.webrender.enabled", true);
user_pref("gfx.webrender.compositor", true);
user_pref("gfx.webrender.software", false);
user_pref("layers.acceleration.force-enabled", true);

// --- Cache / disk tuning ---
user_pref("browser.cache.disk.capacity", 262144);
user_pref("browser.cache.disk.smart_size.first_run", false);
user_pref("browser.cache.disk.enable", true);
user_pref("browser.cache.memory.enable", true);
user_pref("browser.cache.memory.capacity", 131072);
// --- Tier-scaled low-commit-space threshold (overridden by Stage 46 per RAM tier: 2048/3072/4096) ---
user_pref("browser.low_commit_space_threshold_mb", 3072);

// --- Telemetry & data collection (off) ---
user_pref("toolkit.telemetry.enabled", false);
user_pref("toolkit.telemetry.unified", false);
user_pref("toolkit.telemetry.server", "");
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.policy.dataSubmissionPolicy.bypassNotification", true);
user_pref("datareporting.policy.dataSubmissionPolicyAccepted", false);
user_pref("browser.newtabpage.activity-stream.feeds.telemetry", false);
user_pref("browser.newtabpage.activity-stream.telemetry", false);

// --- Privacy / tracking ---
user_pref("privacy.trackingprotection.enabled", true);
user_pref("privacy.trackingprotection.pbmode.enabled", true);
user_pref("privacy.dontexpandtracking", true);
user_pref("privacy.clearOnShutdown.cache", true);

// --- Network / connection ---
user_pref("network.http.connection-timeout", 30);
user_pref("network.http.max-connections-per-server", 12);
user_pref("network.http.max-persistent-connections-per-server", 8);

// --- UI responsiveness ---
user_pref("browser.preferences.animateFadeIn", 0);
user_pref("browser.tabs.unloadOnLowMemory", true);

// --- DRM (RDD) sandbox — KEPT ENABLED. MOZ_DISABLE_RDD_SANDBOX is NOT set.
// user_pref("media.eme.advanced", false);  // intentionally omitted
// <<< MACUBUNTU-PERF-END
