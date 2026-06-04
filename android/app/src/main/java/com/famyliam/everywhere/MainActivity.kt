package com.famyliam.everywhere

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import java.util.UUID
import android.view.GestureDetector
import android.view.MotionEvent
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GestureDetectorCompat
import com.famyliam.everywhere.bridge.FamyliamJsBridge
import com.famyliam.everywhere.databinding.ActivityMainBinding
import com.famyliam.everywhere.playback.PlaybackService
import com.famyliam.everywhere.util.ServerUrlNormalizer

class MainActivity : AppCompatActivity() {
    companion object {
        /** Safety net when WebView evaluateJavascript callback is lost — not handoff business delay. */
        private const val HANDOFF_PAUSE_FALLBACK_MS = 400L
    }

    private lateinit var binding: ActivityMainBinding
    private lateinit var jsBridge: FamyliamJsBridge
    private var serverUrl: String = ""
    private var serverOrigin: String = ""
    private lateinit var gestureDetector: GestureDetectorCompat
    private val mainHandler = Handler(Looper.getMainLooper())
    private var pauseFallbackRunnable: Runnable? = null
    private var backgroundHandoffRequestId: String? = null
    private var backgroundHandoffRequestedInPause = false
    private var webViewPauseCompleted = false

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotificationPermissionIfNeeded()

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        serverUrl = ServerUrlNormalizer.normalize(ServerPreferences.getServerUrl(this).orEmpty())
        if (serverUrl.isBlank()) {
            startActivity(Intent(this, ServerSetupActivity::class.java))
            finish()
            return
        }
        serverOrigin = ServerUrlNormalizer.extractOrigin(serverUrl)

        jsBridge = FamyliamJsBridge(this) { script ->
            runOnUiThread {
                binding.webView.evaluateJavascript(script, null)
            }
        }
        jsBridge.setBackgroundHandoffFinishedListener { requestId, _ ->
            onBackgroundHandoffFinished(requestId)
        }

        gestureDetector = GestureDetectorCompat(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onLongPress(e: MotionEvent) {
                startActivity(Intent(this@MainActivity, ServerSetupActivity::class.java))
            }
        })

        configureWebView(binding.webView)
        startPlaybackService()
        binding.webView.loadUrl(serverUrl)
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            == PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView(webView: WebView) {
        val cookieManager = CookieManager.getInstance()
        cookieManager.setAcceptCookie(true)
        cookieManager.setAcceptThirdPartyCookies(webView, true)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            webView.setRendererPriorityPolicy(
                WebView.RENDERER_PRIORITY_IMPORTANT,
                true
            )
        }

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = true
            allowContentAccess = true
            offscreenPreRaster = true
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
            }
        }
        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                return false
            }
        }
        webView.addJavascriptInterface(jsBridge, "FamyliamAndroidBridge")
        webView.setOnTouchListener { _, event ->
            gestureDetector.onTouchEvent(event)
            false
        }
    }

    private fun startPlaybackService() {
        startService(Intent(this, PlaybackService::class.java))
        binding.webView.post { bindPlaybackCallbacks() }
    }

    private fun bindPlaybackCallbacks() {
        PlaybackService.instance?.bindWebCallbacks(
            origin = serverOrigin,
            dispatcher = { detailJson -> jsBridge.dispatchNativeEvent(detailJson) },
            visible = !isFinishing
        )
    }

    override fun onResume() {
        super.onResume()
        cancelWebViewPauseFallback()
        backgroundHandoffRequestId = null
        webViewPauseCompleted = false
        backgroundHandoffRequestedInPause = false
        binding.webView.onResume()
        bindPlaybackCallbacks()
        PlaybackService.instance?.setAppVisible(true)
        when (ServerPreferences.getForegroundResumeMode(this)) {
            ServerPreferences.FOREGROUND_RESUME_NATIVE -> {
                binding.webView.evaluateJavascript(
                    """
                    (function(){
                      if (window.FamyliamAndroidBridge && window.FamyliamAndroidBridge.getPlaybackSnapshot) {
                        var raw = window.FamyliamAndroidBridge.getPlaybackSnapshot();
                        if (window.__famyliamSyncSnapshotFromNative) {
                          window.__famyliamSyncSnapshotFromNative(raw);
                        }
                      }
                    })();
                    """.trimIndent(),
                    null
                )
            }
            else -> {
                binding.webView.evaluateJavascript(
                    "window.FamyliamAndroidBridge && window.FamyliamAndroidBridge.handoffToWeb && window.FamyliamAndroidBridge.handoffToWeb();",
                    null
                )
            }
        }
    }

    override fun onPause() {
        // Web→native handoff first; WebView.onPause() deferred to handoff callback or fallback only.
        cancelWebViewPauseFallback()
        backgroundHandoffRequestedInPause = true
        webViewPauseCompleted = false
        val requestId = UUID.randomUUID().toString()
        backgroundHandoffRequestId = requestId
        requestBackgroundHandoff(requestId)
        val fallbackRequestId = requestId
        pauseFallbackRunnable = Runnable {
            if (webViewPauseCompleted) return@Runnable
            if (fallbackRequestId != backgroundHandoffRequestId) return@Runnable
            pauseWebViewSafely()
        }
        mainHandler.postDelayed(pauseFallbackRunnable!!, HANDOFF_PAUSE_FALLBACK_MS)
        super.onPause()
    }

    private fun onBackgroundHandoffFinished(requestId: String) {
        if (requestId != backgroundHandoffRequestId) return
        pauseWebViewSafely()
    }

    private fun cancelWebViewPauseFallback() {
        pauseFallbackRunnable?.let(mainHandler::removeCallbacks)
        pauseFallbackRunnable = null
    }

    private fun pauseWebViewSafely() {
        if (webViewPauseCompleted || isFinishing || isDestroyed) return
        webViewPauseCompleted = true
        cancelWebViewPauseFallback()
        binding.webView.onPause()
    }

    override fun onStop() {
        PlaybackService.instance?.setAppVisible(false)
        CookieManager.getInstance().flush()
        if (!backgroundHandoffRequestedInPause) {
            requestBackgroundHandoff()
        }
        backgroundHandoffRequestedInPause = false
        super.onStop()
    }

    /**
     * Triggers web→native background handoff. Always runs regardless of foreground resume mode
     * (including native_audio); JS bridge dedupes repeat calls when onStop follows onPause.
     *
     * @param requestId When set, JS notifies [FamyliamJsBridge.notifyBackgroundHandoffFinished]
     *   so MainActivity can defer [WebView.onPause] until handoff completes.
     */
    private fun requestBackgroundHandoff(requestId: String? = null) {
        val jsArg = requestId?.let { id ->
            "'${id.replace("\\", "\\\\").replace("'", "\\'")}'"
        } ?: "null"
        try {
            binding.webView.evaluateJavascript(
                "window.__famyliamRequestBackgroundHandoff && window.__famyliamRequestBackgroundHandoff($jsArg);",
                null
            )
        } catch (_: Exception) {
            // ignore
        }
    }

    override fun onDestroy() {
        cancelWebViewPauseFallback()
        backgroundHandoffRequestId = null
        webViewPauseCompleted = true
        binding.webView.removeJavascriptInterface("FamyliamAndroidBridge")
        binding.webView.destroy()
        super.onDestroy()
    }
}
