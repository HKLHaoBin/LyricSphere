package com.famyliam.everywhere

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
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
    private lateinit var binding: ActivityMainBinding
    private lateinit var jsBridge: FamyliamJsBridge
    private var serverUrl: String = ""
    private var serverOrigin: String = ""
    private lateinit var gestureDetector: GestureDetectorCompat

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotificationPermissionIfNeeded()

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        serverUrl = ServerPreferences.getServerUrl(this).orEmpty()
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

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = true
            allowContentAccess = true
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
        startForegroundService(Intent(this, PlaybackService::class.java))
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
        bindPlaybackCallbacks()
        PlaybackService.instance?.setAppVisible(true)
        binding.webView.evaluateJavascript(
            "window.FamyliamAndroidBridge && window.FamyliamAndroidBridge.handoffToWeb && window.FamyliamAndroidBridge.handoffToWeb();",
            null
        )
    }

    override fun onStop() {
        PlaybackService.instance?.setAppVisible(false)
        CookieManager.getInstance().flush()
        binding.webView.evaluateJavascript(
            "window.__famyliamRequestBackgroundHandoff && window.__famyliamRequestBackgroundHandoff();",
            null
        )
        super.onStop()
    }

    override fun onDestroy() {
        binding.webView.removeJavascriptInterface("FamyliamAndroidBridge")
        binding.webView.destroy()
        super.onDestroy()
    }
}
