package com.famyliam.everywhere.bridge

import android.content.Context
import android.content.Intent
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import com.famyliam.everywhere.playback.PayloadJson
import com.famyliam.everywhere.playback.PlaybackService
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class FamyliamJsBridge(
    private val context: Context,
    private val jsDispatcher: (String) -> Unit
) {
    private val refreshCallbacks = ConcurrentHashMap<String, (String?) -> Unit>()

    @JavascriptInterface
    fun syncQueue(jsonPayload: String) {
        startService(PlaybackService.ACTION_SYNC_QUEUE, jsonPayload)
    }

    @JavascriptInterface
    fun handoffToNative(jsonPayload: String) {
        CookieManager.getInstance().flush()
        startService(PlaybackService.ACTION_HANDOFF_TO_NATIVE, jsonPayload)
    }

    @JavascriptInterface
    fun handoffToWeb(): String {
        val snapshotJson = PlaybackService.instance?.performHandoffToWeb() ?: "{}"
        dispatchNativeEvent(snapshotJson)
        return snapshotJson
    }

    /**
     * Called from native playback on 401 / URL expiry; asks the web layer for a fresh signed URL.
     */
    fun refreshTrackUrl(filename: String, callback: (String?) -> Unit) {
        val requestId = UUID.randomUUID().toString()
        refreshCallbacks[requestId] = callback
        val quoted = JSONObject.quote(filename)
        val script = """
            (function(){
              var done = function(url) {
                try {
                  FamyliamAndroidBridge.onRefreshTrackUrlResult('$requestId', url || '');
                } catch (e) {}
              };
              try {
                if (typeof window.__famyliamRefreshTrackUrl === 'function') {
                  Promise.resolve(window.__famyliamRefreshTrackUrl($quoted))
                    .then(done)
                    .catch(function() { done(''); });
                } else {
                  done('');
                }
              } catch (e) { done(''); }
            })();
        """.trimIndent()
        jsDispatcher(script)
    }

    @JavascriptInterface
    fun onRefreshTrackUrlResult(requestId: String, audioUrl: String) {
        refreshCallbacks.remove(requestId)?.invoke(audioUrl.takeIf { it.isNotBlank() })
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

    private fun startService(action: String, json: String?) {
        val intent = Intent(context, PlaybackService::class.java).setAction(action)
        if (!json.isNullOrBlank()) {
            intent.putExtra(PlaybackService.EXTRA_JSON, json)
        }
        context.startForegroundService(intent)
    }
}
