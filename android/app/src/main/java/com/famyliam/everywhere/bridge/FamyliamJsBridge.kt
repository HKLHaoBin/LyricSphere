package com.famyliam.everywhere.bridge

import android.content.Context
import android.content.Intent
import android.os.Build
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import androidx.core.content.ContextCompat
import com.famyliam.everywhere.ServerPreferences
import com.famyliam.everywhere.playback.PlaybackService

class FamyliamJsBridge(
    private val context: Context,
    private val jsDispatcher: (String) -> Unit
) {
    @JavascriptInterface
    fun syncQueue(jsonPayload: String) {
        startPlaybackServiceForSync(jsonPayload)
    }

    @JavascriptInterface
    fun handoffToNative(jsonPayload: String) {
        CookieManager.getInstance().flush()
        startPlaybackServiceForHandoff(jsonPayload)
    }

    @JavascriptInterface
    fun handoffToWeb(): String {
        val snapshotJson = PlaybackService.instance?.performHandoffToWeb() ?: "{}"
        val mode = ServerPreferences.getForegroundResumeMode(context)
        if (mode != ServerPreferences.FOREGROUND_RESUME_NATIVE) {
            dispatchNativeEvent(snapshotJson)
        }
        return snapshotJson
    }

    @JavascriptInterface
    fun getPlaybackSnapshot(): String {
        return PlaybackService.instance?.getPlaybackSnapshotJson() ?: "{}"
    }

    @JavascriptInterface
    fun getForegroundResumeMode(): String {
        return ServerPreferences.getForegroundResumeMode(context)
    }

    fun dispatchNativeEvent(detailJson: String) {
        val encoded = java.net.URLEncoder.encode(detailJson, "UTF-8")
        val script = """
            (function(){
              try {
                var detail = JSON.parse(decodeURIComponent('$encoded'));
                window.dispatchEvent(new CustomEvent('famyliam-native-playback-state', { detail: detail }));
              } catch (e) {}
            })();
        """.trimIndent()
        jsDispatcher(script)
    }

    private fun buildPlaybackIntent(action: String, json: String?): Intent {
        val intent = Intent(context, PlaybackService::class.java).setAction(action)
        if (!json.isNullOrBlank()) {
            intent.putExtra(PlaybackService.EXTRA_JSON, json)
        }
        return intent
    }

    /** Queue sync only; must not use startForegroundService (no FGS timeout). */
    private fun startPlaybackServiceForSync(json: String?) {
        context.startService(buildPlaybackIntent(PlaybackService.ACTION_SYNC_QUEUE, json))
    }

    /** Native handoff may play in background; requires foreground service on API 26+. */
    private fun startPlaybackServiceForHandoff(json: String?) {
        val intent = buildPlaybackIntent(PlaybackService.ACTION_HANDOFF_TO_NATIVE, json)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            ContextCompat.startForegroundService(context, intent)
        } else {
            context.startService(intent)
        }
    }
}
