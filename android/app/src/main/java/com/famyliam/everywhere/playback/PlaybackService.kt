package com.famyliam.everywhere.playback

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import android.webkit.CookieManager
import androidx.core.app.ServiceCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.CommandButton
import androidx.media3.session.ConnectionResult
import androidx.media3.session.DefaultMediaNotificationProvider
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionCommands
import androidx.media3.session.SessionResult
import com.famyliam.everywhere.ServerPreferences
import com.famyliam.everywhere.R
import com.famyliam.everywhere.util.ArtworkCache
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
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

        val SESSION_COMMAND_SKIP_PREV = SessionCommand(ACTION_SKIP_PREV, Bundle.EMPTY)
        val SESSION_COMMAND_SKIP_NEXT = SessionCommand(ACTION_SKIP_NEXT, Bundle.EMPTY)

        @Volatile
        var instance: PlaybackService? = null
    }

    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val queue = QueueEngine()
    private val stateStore by lazy { PlaybackStateStore(this) }
    private val artworkCache by lazy { ArtworkCache(this, executor) }
    private var mediaSession: MediaSession? = null
    private var controller: PlaybackController? = null
    private var audioFocusHandler: AudioFocusHandler? = null
    private var serverOrigin: String = ""
    private var webEventDispatcher: ((String) -> Unit)? = null
    private var appVisible: Boolean = true
    private var amllMode: String = "local"
    @Volatile
    private var nativeActive: Boolean = false
    @Volatile
    private var handoffInProgress: Boolean = false
    private var lastProgressDispatchMs: Long = 0L

    override fun onCreate() {
        super.onCreate()
        instance = this
        ensureNotificationChannel()
        setMediaNotificationProvider(
            DefaultMediaNotificationProvider.Builder(this)
                .setNotificationId(NOTIFICATION_ID)
                .setChannelId(CHANNEL_ID)
                .build()
        )
        audioFocusHandler = AudioFocusHandler(
            context = this,
            onFocusLoss = { controller?.pause() },
            onBecomingNoisy = { controller?.pause() }
        )
        controller = PlaybackController(
            context = this,
            executor = executor,
            artworkCache = artworkCache,
            onEnded = { handleTrackEnded() },
            onError = { message -> handlePlaybackError(message) },
            onIsPlayingChanged = { playing ->
                if (playing) ensureForegroundStarted() else maybeStopForegroundAndSelf()
                persistStateThrottled()
                requestNotificationRefresh()
            },
            onProgressTick = {
                persistStateThrottled()
                maybeDispatchProgressToWeb()
                requestNotificationRefresh()
            },
            onMediaItemTransition = { item, _ -> handleMediaItemTransition(item) }
        )
        mediaSession = MediaSession.Builder(this, controller!!.player)
            .setCallback(sessionCallback)
            .setMediaButtonPreferences(buildMediaButtonPreferences())
            .build()
        restoreColdStartState()
    }

    private val sessionCallback = object : MediaSession.Callback {
        override fun onConnect(
            session: MediaSession,
            controllerInfo: MediaSession.ControllerInfo
        ): ConnectionResult {
            return ConnectionResult.AcceptedResultBuilder(session)
                .setAvailablePlayerCommands(availablePlayerCommands())
                .setAvailableSessionCommands(
                    SessionCommands.Builder()
                        .add(SESSION_COMMAND_SKIP_PREV)
                        .add(SESSION_COMMAND_SKIP_NEXT)
                        .build()
                )
                .build()
        }

        override fun onCustomCommand(
            session: MediaSession,
            controllerInfo: MediaSession.ControllerInfo,
            command: SessionCommand,
            args: Bundle
        ): ListenableFuture<SessionResult> {
            when (command.customAction) {
                ACTION_SKIP_PREV -> {
                    skipPrev()
                    requestNotificationRefresh()
                    return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
                }
                ACTION_SKIP_NEXT -> {
                    skipNext()
                    requestNotificationRefresh()
                    return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
                }
            }
            return super.onCustomCommand(session, controllerInfo, command, args)
        }

        override fun onPlaybackResumption(
            mediaSession: MediaSession,
            controller: MediaSession.ControllerInfo,
            isForPlayback: Boolean
        ): ListenableFuture<MediaSession.MediaItemsWithStartPosition> {
            val resumption = buildResumptionFromStore()
                ?: return Futures.immediateFailedFuture(
                    UnsupportedOperationException("no_saved_playback")
                )
            if (isForPlayback) {
                nativeActive = true
                val filename = queue.currentFilename
                if (filename.isNotBlank()) {
                    prefetchAroundCurrent(filename)
                }
                persistState(force = true)
                requestNotificationRefresh()
            }
            return Futures.immediateFuture(resumption)
        }

        override fun onMediaButtonEvent(
            session: MediaSession,
            controllerInfo: MediaSession.ControllerInfo,
            intent: Intent
        ): Boolean {
            val keyEvent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT, KeyEvent::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT) as? KeyEvent
            } ?: return super.onMediaButtonEvent(session, controllerInfo, intent)
            if (keyEvent.action != KeyEvent.ACTION_DOWN) {
                return super.onMediaButtonEvent(session, controllerInfo, intent)
            }
            when (keyEvent.keyCode) {
                KeyEvent.KEYCODE_MEDIA_NEXT -> {
                    skipNext()
                    requestNotificationRefresh()
                    return true
                }
                KeyEvent.KEYCODE_MEDIA_PREVIOUS -> {
                    skipPrev()
                    requestNotificationRefresh()
                    return true
                }
            }
            return super.onMediaButtonEvent(session, controllerInfo, intent)
        }

        override fun onPlayerInteractionFinished(
            session: MediaSession,
            controllerInfo: MediaSession.ControllerInfo,
            playerCommands: Player.Commands
        ) {
            val player = controller?.player ?: return
            if (playerCommands.contains(Player.COMMAND_PLAY_PAUSE) &&
                player.playWhenReady &&
                currentItemNeedsAudioResolve()
            ) {
                player.playWhenReady = false
                val filename = queue.currentFilename.ifBlank {
                    player.currentMediaItem?.mediaId.orEmpty()
                }
                if (filename.isNotBlank()) {
                    resolveAndPlay(filename, autoPlay = true)
                }
            }
        }
    }

    private fun availablePlayerCommands(): Player.Commands {
        return Player.Commands.Builder()
            .add(Player.COMMAND_PLAY_PAUSE)
            .add(Player.COMMAND_SEEK_IN_CURRENT_MEDIA_ITEM)
            .add(Player.COMMAND_GET_CURRENT_MEDIA_ITEM)
            .add(Player.COMMAND_CHANGE_MEDIA_ITEMS)
            .build()
    }

    private fun requestNotificationRefresh() {
        if (mediaSession == null) return
        triggerNotificationUpdate()
    }

    private fun buildMediaButtonPreferences(): List<CommandButton> {
        return listOf(
            CommandButton.Builder(CommandButton.ICON_SKIP_BACK)
                .setDisplayName(getString(R.string.action_previous))
                .setSessionCommand(SESSION_COMMAND_SKIP_PREV)
                .setSlots(CommandButton.SLOT_BACK)
                .build(),
            CommandButton.Builder(CommandButton.ICON_SKIP_FORWARD)
                .setDisplayName(getString(R.string.action_next))
                .setSessionCommand(SESSION_COMMAND_SKIP_NEXT)
                .setSlots(CommandButton.SLOT_FORWARD)
                .build()
        )
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? {
        return mediaSession
    }

    override fun onUpdateNotification(
        session: MediaSession,
        startInForegroundRequired: Boolean
    ) {
        super.onUpdateNotification(session, startInForegroundRequired)
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

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (hasForegroundPlaybackState()) {
            ensureForegroundStarted()
        } else {
            stopForegroundAndSelf()
        }
        super.onTaskRemoved(rootIntent)
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
        if (visible && nativeActive) {
            dispatchSnapshotToWeb(controller?.currentSnapshot(queue) ?: PlaybackSnapshot())
        }
    }

    fun setAppVisible(visible: Boolean) {
        appVisible = visible
    }

    fun getPlaybackSnapshotJson(): String {
        val snapshot = controller?.currentSnapshot(queue)?.copy(
            amllMode = amllMode
        ) ?: PlaybackSnapshot(amllMode = amllMode)
        return PayloadJson.snapshotToEventJson(snapshot)
    }

    fun performHandoffToWeb(): String {
        val mode = ServerPreferences.getForegroundResumeMode(this)
        if (mode == ServerPreferences.FOREGROUND_RESUME_NATIVE) {
            return getPlaybackSnapshotJson()
        }
        nativeActive = false
        val snapshot = controller?.currentSnapshot(queue) ?: PlaybackSnapshot()
        controller?.pause()
        audioFocusHandler?.abandonFocus()
        val json = PayloadJson.snapshotToEventJson(snapshot)
        if (appVisible) {
            dispatchSnapshotToWeb(snapshot)
        }
        persistState(force = true)
        if (!hasForegroundPlaybackState()) {
            stopForegroundAndSelf()
        } else {
            requestNotificationRefresh()
        }
        return json
    }

    private fun handleSyncQueue(json: String) {
        val payload = PayloadJson.parseHandoff(json) ?: return
        if (!isSupportedMode(payload.amllMode)) return
        amllMode = payload.amllMode
        queue.syncQueue(payload)
        if (nativeActive) {
            applyQueueToPlayer(payload, autoPlay = controller?.player?.isPlaying == true)
        }
        persistStateThrottled()
    }

    private fun handleHandoffToNative(json: String) {
        if (handoffInProgress) {
            if (hasForegroundPlaybackState()) {
                ensureForegroundStarted()
            }
            return
        }
        handoffInProgress = true
        try {
            val payload = PayloadJson.parseHandoff(json) ?: run {
                dispatchErrorToWeb("invalid_payload")
                endFailedHandoffIfIdle()
                return
            }
            if (!isSupportedMode(payload.amllMode)) {
                endFailedHandoffIfIdle()
                return
            }
            amllMode = payload.amllMode
            if (payload.track.audioUrl.isBlank()) {
                dispatchErrorToWeb("missing_audio_url")
                endFailedHandoffIfIdle()
                return
            }
            if (nativeActive && controller?.player?.isPlaying == true) {
                queue.updateFromPayload(payload)
                applyQueueToPlayer(payload, autoPlay = true)
                persistStateThrottled()
                ensureForegroundStarted()
                return
            }
            CookieManager.getInstance().flush()
            queue.updateFromPayload(payload)
            audioFocusHandler?.requestFocus()
            val repeatSingle = payload.playbackMode == "single"
            val ok = applyQueueToPlayer(payload, autoPlay = payload.isPlaying, repeatSingle = repeatSingle)
            if (!ok) {
                dispatchErrorToWeb("native_playback_failed")
                endFailedHandoffIfIdle()
                return
            }
            nativeActive = true
            queue.onTrackStarted(payload.filename)
            ensureForegroundStarted()
            prefetchAroundCurrent(payload.filename)
            persistState(force = true)
            requestNotificationRefresh()
        } finally {
            handoffInProgress = false
        }
    }

    /**
     * startForegroundService requires startForeground within ~5s on API 26+.
     * If handoff fails before native playback is active, tear down when nothing else needs the service.
     */
    private fun endFailedHandoffIfIdle() {
        if (hasForegroundPlaybackState()) {
            ensureForegroundStarted()
        } else {
            stopForegroundAndSelf()
        }
    }

    private fun applyQueueToPlayer(
        payload: HandoffPayload,
        autoPlay: Boolean,
        repeatSingle: Boolean = payload.playbackMode == "single"
    ): Boolean {
        val ctrl = controller ?: return false
        val items = buildMediaItemsForQueue()
        if (items.isEmpty()) {
            return ctrl.playTrack(
                track = payload.track,
                startSec = payload.currentTime,
                autoPlay = autoPlay,
                repeatSingle = repeatSingle
            )
        }
        var startIndex = items.indexOfFirst { it.mediaId == payload.filename }
        if (startIndex < 0) startIndex = 0
        val startMs = (payload.currentTime.coerceAtLeast(0.0) * 1000).toLong()
        ctrl.setQueue(items, startIndex, startMs, autoPlay, repeatSingle)
        queue.onTrackStarted(payload.filename)
        prefetchAroundCurrent(payload.filename)
        return true
    }

    private fun buildMediaItemsForQueue(): List<MediaItem> {
        val ctrl = controller ?: return emptyList()
        return queue.queueIds.map { filename ->
            val track = queue.trackFor(filename) ?: TrackMeta(filename)
            ctrl.buildMediaItem(track)
        }
    }

    private fun handleMediaItemTransition(item: MediaItem?) {
        val filename = item?.mediaId.orEmpty()
        if (filename.isBlank()) return
        queue.onTrackStarted(filename)
        val uri = item?.localConfiguration?.uri
        if (uri == null) {
            resolveAndPlay(filename, autoPlay = controller?.player?.isPlaying == true)
            return
        }
        prefetchAroundCurrent(filename)
        persistStateThrottled()
        requestNotificationRefresh()
    }

    private fun handleTrackEnded() {
        if (queue.playbackMode == "single") {
            controller?.seekToSec(0.0)
            controller?.play()
            return
        }
        val player = controller?.player ?: return
        val currentIndex = controller?.currentIndex() ?: -1
        if (queue.playbackMode != "shuffle" &&
            player.mediaItemCount > 1 &&
            currentIndex >= 0 &&
            currentIndex < player.mediaItemCount - 1
        ) {
            val nextIndex = currentIndex + 1
            val nextId = player.getMediaItemAt(nextIndex).mediaId
            if (!nextId.isNullOrBlank()) {
                queue.onTrackStarted(nextId)
                controller?.seekToMediaIndex(nextIndex, autoPlay = true)
                prefetchAroundCurrent(nextId)
                return
            }
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
        playFilenameInPlaylistOrResolve(nextFilename, autoPlay = true)
    }

    private fun skipPrev() {
        val prevFilename = queue.pickPrev() ?: return
        playFilenameInPlaylistOrResolve(prevFilename, autoPlay = true)
    }

    private fun playFilenameInPlaylistOrResolve(filename: String, autoPlay: Boolean) {
        val index = controller?.indexOfMediaId(filename) ?: -1
        val item = if (index >= 0) controller?.player?.getMediaItemAt(index) else null
        val hasUri = item?.localConfiguration?.uri != null
        if (index >= 0 && hasUri) {
            queue.onTrackStarted(filename)
            controller?.seekToMediaIndex(index, autoPlay)
            prefetchAroundCurrent(filename)
            persistStateThrottled()
            requestNotificationRefresh()
            return
        }
        resolveAndPlay(filename, autoPlay)
    }

    private fun currentItemNeedsAudioResolve(): Boolean {
        val item = controller?.player?.currentMediaItem ?: return false
        return item.localConfiguration?.uri == null
    }

    private fun togglePlayback() {
        val player = controller?.player ?: return
        if (player.isPlaying) {
            controller?.pause()
        } else if (currentItemNeedsAudioResolve()) {
            val filename = queue.currentFilename.ifBlank { player.currentMediaItem?.mediaId.orEmpty() }
            if (filename.isNotBlank()) {
                resolveAndPlay(filename, autoPlay = true)
            }
        } else {
            audioFocusHandler?.requestFocus()
            controller?.play()
            ensureForegroundStarted()
        }
        persistStateThrottled()
        requestNotificationRefresh()
    }

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
                    queue.cacheTrack(filename, track)
                    playResolvedTrack(track, autoPlay)
                } else {
                    handlePlaybackError("track_resolve_failed")
                }
            }
        }
    }

    private fun playResolvedTrack(track: TrackMeta, autoPlay: Boolean) {
        val repeatSingle = queue.playbackMode == "single"
        val index = controller?.indexOfMediaId(track.filename) ?: -1
        if (index >= 0) {
            val item = controller?.buildMediaItem(track)
            if (item != null) {
                controller?.updateItemAt(index, item)
                controller?.seekToMediaIndex(index, autoPlay)
                controller?.loadArtworkForTrack(track, index)
            }
        } else {
            controller?.playTrack(track, 0.0, autoPlay, repeatSingle)
        }
        nativeActive = true
        audioFocusHandler?.requestFocus()
        ensureForegroundStarted()
        prefetchAroundCurrent(track.filename)
        persistState(force = true)
        requestNotificationRefresh()
    }

    private fun prefetchAroundCurrent(filename: String) {
        prefetchSongInfoFor(filename)
        val nextFilename = queue.peekNextForPrefetch()
        if (!nextFilename.isNullOrBlank() && nextFilename != filename) {
            prefetchSongInfoFor(nextFilename)
        }
    }

    private fun prefetchSongInfoFor(filename: String) {
        if (filename.isBlank() || serverOrigin.isBlank()) return
        executor.execute {
            val fresh = fetchSongInfo(filename)
            if (fresh == null) return@execute
            val cached = queue.trackFor(filename)
            val merged = SongInfoResolver.mergeWithCache(fresh, cached)
            mainHandler.post {
                queue.cacheTrack(filename, merged)
                val index = controller?.indexOfMediaId(filename) ?: -1
                if (index >= 0) {
                    val item = controller?.buildMediaItem(merged)
                    if (item != null) {
                        controller?.updateItemAt(index, item)
                        controller?.loadArtworkForTrack(merged, index)
                    }
                }
                requestNotificationRefresh()
            }
        }
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
                        requestNotificationRefresh()
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
        }
        persistState(force = true)
        requestNotificationRefresh()
    }

    private fun dispatchSnapshotToWeb(snapshot: PlaybackSnapshot) {
        webEventDispatcher?.invoke(PayloadJson.snapshotToEventJson(snapshot.copy(amllMode = amllMode)))
    }

    private fun dispatchErrorToWeb(message: String) {
        dispatchSnapshotToWeb(
            controller?.currentSnapshot(queue)?.copy(error = message, isPlaying = false)
                ?: PlaybackSnapshot(error = message)
        )
    }

    private fun maybeDispatchProgressToWeb() {
        if (!nativeActive || !appVisible) return
        val mode = ServerPreferences.getForegroundResumeMode(this)
        if (mode != ServerPreferences.FOREGROUND_RESUME_NATIVE) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastProgressDispatchMs < 1000L) return
        lastProgressDispatchMs = now
        dispatchSnapshotToWeb(controller?.currentSnapshot(queue) ?: return)
    }

    private fun isSupportedMode(mode: String): Boolean {
        return mode == "local" || mode == "am-style"
    }

    /** Persisted snapshot only — cold start / [onPlaybackResumption], not FGS retention. */
    private fun hasRecoverableState(): Boolean = stateStore.hasRecoverableState()

    /**
     * Native playback needs a foreground notification (FGS), distinct from recoverable SP state.
     */
    private fun hasForegroundPlaybackState(): Boolean {
        if (!nativeActive) return false
        val player = controller?.player ?: return false
        if (player.isPlaying) return true
        return when (player.playbackState) {
            Player.STATE_BUFFERING -> true
            Player.STATE_READY ->
                queue.queueIds.isNotEmpty() || player.mediaItemCount > 0
            else -> false
        }
    }

    private fun ensureForegroundStarted() {
        requestNotificationRefresh()
    }

    private fun maybeStopForegroundAndSelf() {
        if (hasForegroundPlaybackState()) return
        stopForegroundAndSelf()
    }

    private fun stopForegroundAndSelf() {
        audioFocusHandler?.abandonFocus()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun persistStateThrottled() {
        persistState(force = false)
    }

    private fun persistState(force: Boolean) {
        val ctrl = controller ?: return
        val snapshot = PlaybackStateStore.fromRuntime(
            serverOrigin = serverOrigin,
            queue = queue,
            controller = ctrl,
            nativeActive = nativeActive,
            amllMode = amllMode
        )
        stateStore.saveThrottled(snapshot, force = force)
    }

    private fun restoreColdStartState() {
        val persisted = stateStore.load() ?: return
        if (!persisted.nativeActive) return
        applyPersistedSnapshot(persisted)
        val resumption = buildResumptionPlaylist(persisted) ?: return
        controller?.setQueue(
            items = resumption.mediaItems,
            startIndex = resumption.startIndex,
            startPositionMs = resumption.startPositionMs,
            autoPlay = false,
            repeatSingle = persisted.playbackMode == "single"
        )
        if (persisted.filename.isNotBlank()) {
            prefetchAroundCurrent(persisted.filename)
        }
        requestNotificationRefresh()
    }

    private fun buildResumptionFromStore(): MediaSession.MediaItemsWithStartPosition? {
        val persisted = stateStore.load() ?: return null
        if (!persisted.nativeActive && persisted.filename.isBlank()) return null
        applyPersistedSnapshot(persisted)
        return buildResumptionPlaylist(persisted)
    }

    private fun applyPersistedSnapshot(persisted: PlaybackStateStore.PersistedSnapshot) {
        if (persisted.serverOrigin.isNotBlank()) {
            serverOrigin = persisted.serverOrigin
            controller?.setServerOrigin(serverOrigin)
        }
        queue.restoreFromPersisted(
            queueIds = persisted.queueIds,
            playbackMode = persisted.playbackMode,
            currentFilename = persisted.filename,
            shuffle = persisted.shuffle,
            trackCache = persisted.trackCache
        )
        nativeActive = persisted.nativeActive || persisted.filename.isNotBlank()
        amllMode = persisted.amllMode
    }

    private fun buildResumptionPlaylist(
        persisted: PlaybackStateStore.PersistedSnapshot
    ): MediaSession.MediaItemsWithStartPosition? {
        val items = buildMediaItemsForQueue()
        val startMs = (persisted.currentTime.coerceAtLeast(0.0) * 1000).toLong()
        if (items.isNotEmpty()) {
            val index = items.indexOfFirst { it.mediaId == persisted.filename }.coerceAtLeast(0)
            return MediaSession.MediaItemsWithStartPosition(items, index, startMs)
        }
        val filename = persisted.filename
        if (filename.isBlank()) return null
        val track = queue.trackFor(filename) ?: TrackMeta(filename)
        val ctrl = controller ?: return null
        return MediaSession.MediaItemsWithStartPosition(
            listOf(ctrl.buildMediaItem(track)),
            0,
            startMs
        )
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

    override fun onDestroy() {
        persistState(force = true)
        instance = null
        mediaSession?.release()
        mediaSession = null
        controller?.release()
        controller = null
        audioFocusHandler?.abandonFocus()
        audioFocusHandler = null
        executor.shutdownNow()
        super.onDestroy()
    }
}
