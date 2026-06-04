package com.famyliam.everywhere.playback

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import androidx.core.app.NotificationCompat
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionResult
import com.famyliam.everywhere.MainActivity
import com.famyliam.everywhere.R
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

@UnstableApi
class PlaybackService : MediaSessionService() {
    companion object {
        const val ACTION_HANDOFF_TO_NATIVE = "com.famyliam.everywhere.HANDOFF_TO_NATIVE"
        const val ACTION_SYNC_QUEUE = "com.famyliam.everywhere.SYNC_QUEUE"
        const val ACTION_HANDOFF_TO_WEB = "com.famyliam.everywhere.HANDOFF_TO_WEB"
        const val ACTION_SKIP_NEXT = "com.famyliam.everywhere.SKIP_NEXT"
        const val ACTION_SKIP_PREV = "com.famyliam.everywhere.SKIP_PREV"
        const val ACTION_TOGGLE = "com.famyliam.everywhere.TOGGLE"
        const val EXTRA_JSON = "extra_json"

        private const val CHANNEL_ID = "famyliam_playback"
        private const val NOTIFICATION_ID = 1001

        @Volatile
        var instance: PlaybackService? = null
    }

    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val queue = QueueEngine()
    private var mediaSession: MediaSession? = null
    private var controller: PlaybackController? = null
    private var serverOrigin: String = ""
    private var webEventDispatcher: ((String) -> Unit)? = null
    private var appVisible: Boolean = true
    @Volatile
    private var nativeActive: Boolean = false
    @Volatile
    private var handoffInProgress: Boolean = false

    override fun onCreate() {
        super.onCreate()
        instance = this
        ensureNotificationChannel()
        controller = PlaybackController(
            context = this,
            onEnded = { handleTrackEnded() },
            onError = { message -> handlePlaybackError(message) },
            onIsPlayingChanged = { updateNotification() }
        )
        mediaSession = MediaSession.Builder(this, controller!!.player)
            .setCallback(sessionCallback)
            .build()
        startForeground(NOTIFICATION_ID, buildNotification())
    }

    /** Media3 1.2.1: intercept skip; play/pause are forwarded to the session player. */
    private val sessionCallback = object : MediaSession.Callback {
        @Deprecated("Deprecated in Media3 1.2.1")
        override fun onPlayerCommandRequest(
            session: MediaSession,
            controllerInfo: MediaSession.ControllerInfo,
            @Player.Command playerCommand: Int
        ): Int {
            when (playerCommand) {
                Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM -> {
                    skipNext()
                    return SessionResult.RESULT_SUCCESS
                }
                Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM -> {
                    skipPrev()
                    return SessionResult.RESULT_SUCCESS
                }
            }
            return SessionResult.RESULT_SUCCESS
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? {
        return mediaSession
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_HANDOFF_TO_NATIVE -> handleHandoffToNative(intent.getStringExtra(EXTRA_JSON).orEmpty())
            ACTION_SYNC_QUEUE -> handleSyncQueue(intent.getStringExtra(EXTRA_JSON).orEmpty())
            ACTION_HANDOFF_TO_WEB -> performHandoffToWeb()
            ACTION_SKIP_NEXT -> skipNext()
            ACTION_SKIP_PREV -> skipPrev()
            ACTION_TOGGLE -> togglePlayback()
        }
        return START_STICKY
    }

    fun bindWebCallbacks(
        origin: String,
        dispatcher: (String) -> Unit,
        visible: Boolean
    ) {
        serverOrigin = origin
        webEventDispatcher = dispatcher
        appVisible = visible
        controller?.setServerOrigin(origin)
    }

    fun setAppVisible(visible: Boolean) {
        appVisible = visible
    }

    fun performHandoffToWeb(): String {
        nativeActive = false
        val snapshot = controller?.currentSnapshot(queue) ?: PlaybackSnapshot()
        controller?.pause()
        val json = PayloadJson.snapshotToEventJson(snapshot)
        if (appVisible) {
            dispatchSnapshotToWeb(snapshot)
        }
        updateNotification()
        return json
    }

    private fun handleSyncQueue(json: String) {
        val payload = PayloadJson.parseHandoff(json) ?: return
        if (!isSupportedMode(payload.amllMode)) return
        queue.syncQueue(payload)
    }

    private fun handleHandoffToNative(json: String) {
        if (handoffInProgress) return
        handoffInProgress = true
        try {
            val payload = PayloadJson.parseHandoff(json) ?: run {
                dispatchErrorToWeb("invalid_payload")
                return
            }
            if (!isSupportedMode(payload.amllMode)) return
            if (payload.track.audioUrl.isBlank()) {
                dispatchErrorToWeb("missing_audio_url")
                return
            }
            if (nativeActive && controller?.player?.isPlaying == true) {
                queue.updateFromPayload(payload)
                return
            }
            CookieManager.getInstance().flush()
            queue.updateFromPayload(payload)
            val repeatSingle = payload.playbackMode == "single"
            val ok = controller?.playTrack(
                track = payload.track,
                startSec = payload.currentTime,
                autoPlay = payload.isPlaying,
                repeatSingle = repeatSingle
            ) ?: false
            if (!ok) {
                dispatchErrorToWeb("native_playback_failed")
                return
            }
            nativeActive = true
            queue.onTrackStarted(payload.filename)
            startForeground(NOTIFICATION_ID, buildNotification())
            updateNotification()
        } finally {
            handoffInProgress = false
        }
    }

    private fun handleTrackEnded() {
        if (queue.playbackMode == "single") {
            controller?.seekToSec(0.0)
            controller?.play()
            return
        }
        val advance = queue.onTrackEndedAdvance() ?: return
        val nextFilename = advance.filename
        if (nextFilename.isBlank()) return
        resolveAndPlay(nextFilename, autoPlay = true)
    }

    private fun skipNext() {
        queue.recordShuffleHistoryBeforeAdvance()
        val nextFilename = queue.pickNext() ?: return
        if (nextFilename.isBlank() || nextFilename == queue.currentFilename) return
        resolveAndPlay(nextFilename, autoPlay = true)
    }

    private fun skipPrev() {
        val prevFilename = queue.pickPrev() ?: return
        resolveAndPlay(prevFilename, autoPlay = true)
    }

    private fun togglePlayback() {
        val player = controller?.player ?: return
        if (player.isPlaying) controller?.pause() else controller?.play()
        updateNotification()
    }

    /**
     * Prefer a fresh signed URL from /song-info; fall back to cached payload audioUrl only on failure.
     */
    private fun resolveAndPlay(filename: String, autoPlay: Boolean) {
        executor.execute {
            val cached = queue.trackFor(filename)
            val fresh = fetchSongInfo(filename)
            val track = when {
                fresh != null -> SongInfoResolver.mergeWithCache(fresh, cached)
                else -> queue.trackWithAudioUrl(filename)
            }
            mainHandler.post {
                if (track != null && track.audioUrl.isNotBlank()) {
                    queue.onTrackStarted(filename)
                    playResolvedTrack(track, autoPlay)
                } else {
                    handlePlaybackError("track_resolve_failed")
                }
            }
        }
    }

    private fun playResolvedTrack(track: TrackMeta, autoPlay: Boolean) {
        val repeatSingle = queue.playbackMode == "single"
        controller?.playTrack(track, 0.0, autoPlay, repeatSingle)
        queue.cacheTrack(track.filename, track)
        nativeActive = true
        startForeground(NOTIFICATION_ID, buildNotification())
        updateNotification()
    }

    private fun fetchSongInfo(filename: String): TrackMeta? {
        if (serverOrigin.isBlank()) return null
        return try {
            val url = URL(
                "$serverOrigin/song-info?file=${java.net.URLEncoder.encode(filename, "UTF-8")}"
            )
            val connection = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 10000
                readTimeout = 10000
                val cookie = CookieManager.getInstance().getCookie(serverOrigin).orEmpty()
                if (cookie.isNotBlank()) setRequestProperty("Cookie", cookie)
            }
            val code = connection.responseCode
            val body = if (code in 200..299) {
                connection.inputStream.bufferedReader().use { it.readText() }
            } else {
                connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
            }
            connection.disconnect()
            if (code !in 200..299) return null
            SongInfoResolver.parseResponse(filename, body, serverOrigin)
        } catch (_: Exception) {
            null
        }
    }

    /** Native HTTP refresh on 401/expired token; no WebView evaluateJavascript. */
    private fun handlePlaybackError(message: String) {
        val isAuthError = message.contains("401") || message == "http_401"
        val filename = queue.currentFilename
        if (isAuthError && filename.isNotBlank() && serverOrigin.isNotBlank()) {
            executor.execute {
                val cached = queue.trackFor(filename)
                val fresh = fetchSongInfo(filename)
                val track = when {
                    fresh != null -> SongInfoResolver.mergeWithCache(fresh, cached)
                    else -> null
                }
                mainHandler.post {
                    if (track != null && track.audioUrl.isNotBlank()) {
                        queue.cacheTrack(filename, track)
                        controller?.updateAudioUrl(track)
                        updateNotification()
                    } else {
                        reportPlaybackError(message)
                    }
                }
            }
            return
        }
        reportPlaybackError(message)
    }

    private fun reportPlaybackError(message: String) {
        val snapshot = controller?.currentSnapshot(queue)?.copy(error = message, isPlaying = false)
            ?: PlaybackSnapshot(error = message)
        if (appVisible) {
            dispatchSnapshotToWeb(snapshot)
        } else {
            updateNotification(error = message)
        }
    }

    private fun dispatchSnapshotToWeb(snapshot: PlaybackSnapshot) {
        webEventDispatcher?.invoke(PayloadJson.snapshotToEventJson(snapshot))
    }

    private fun dispatchErrorToWeb(message: String) {
        dispatchSnapshotToWeb(
            controller?.currentSnapshot(queue)?.copy(error = message, isPlaying = false)
                ?: PlaybackSnapshot(error = message)
        )
    }

    private fun isSupportedMode(mode: String): Boolean {
        return mode == "local" || mode == "am-style"
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = getString(R.string.notification_channel_desc)
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(error: String? = null): Notification {
        val session = mediaSession
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val snapshot = controller?.currentSnapshot(queue)
        val title = snapshot?.track?.title?.ifBlank { getString(R.string.app_name) }
            ?: getString(R.string.app_name)
        val text = when {
            error != null -> getString(R.string.notification_error)
            else -> snapshot?.track?.artist?.ifBlank { getString(R.string.notification_playing) }
                ?: getString(R.string.notification_playing)
        }

        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentIntent(openIntent)
            .setOngoing(controller?.player?.isPlaying == true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)

        if (session != null) {
            builder.setStyle(
                androidx.media3.session.MediaStyleNotificationHelper.MediaStyle(session)
                    .setShowActionsInCompactView(0, 1, 2)
            )
        }

        builder.addAction(
            NotificationCompat.Action(
                android.R.drawable.ic_media_previous,
                getString(R.string.action_previous),
                pendingAction(ACTION_SKIP_PREV)
            )
        )
        builder.addAction(
            NotificationCompat.Action(
                if (controller?.player?.isPlaying == true) {
                    android.R.drawable.ic_media_pause
                } else {
                    android.R.drawable.ic_media_play
                },
                if (controller?.player?.isPlaying == true) {
                    getString(R.string.action_pause)
                } else {
                    getString(R.string.action_play)
                },
                pendingAction(ACTION_TOGGLE)
            )
        )
        builder.addAction(
            NotificationCompat.Action(
                android.R.drawable.ic_media_next,
                getString(R.string.action_next),
                pendingAction(ACTION_SKIP_NEXT)
            )
        )
        return builder.build()
    }

    private fun updateNotification(error: String? = null) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, buildNotification(error))
    }

    private fun pendingAction(action: String): PendingIntent {
        val intent = Intent(this, PlaybackService::class.java).setAction(action)
        return PendingIntent.getService(
            this,
            action.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    override fun onDestroy() {
        instance = null
        mediaSession?.release()
        mediaSession = null
        controller?.release()
        controller = null
        executor.shutdownNow()
        super.onDestroy()
    }
}
