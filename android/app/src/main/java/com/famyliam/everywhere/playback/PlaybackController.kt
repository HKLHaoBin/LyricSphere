package com.famyliam.everywhere.playback

import android.content.Context
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.famyliam.everywhere.util.ArtworkCache
import com.famyliam.everywhere.util.CookieAwareDataSource
import java.util.concurrent.Executor

@OptIn(UnstableApi::class)
class PlaybackController(
    context: Context,
    private val executor: Executor,
    private val artworkCache: ArtworkCache,
    private val onEnded: () -> Unit,
    private val onError: (String) -> Unit,
    private val onIsPlayingChanged: (Boolean) -> Unit,
    private val onProgressTick: () -> Unit,
    private val onMediaItemTransition: (MediaItem?, Int) -> Unit,
    private val onSkipNext: () -> Unit,
    private val onSkipPrevious: () -> Unit,
) {
    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private var serverOrigin: String = ""
    private var currentTrack: TrackMeta = TrackMeta("")
    private var dataSourceFactory = CookieAwareDataSource(appContext) { serverOrigin }

    private fun buildPlayer(): ExoPlayer {
        val mediaSourceFactory = DefaultMediaSourceFactory(dataSourceFactory)
        return ExoPlayer.Builder(appContext)
            .setMediaSourceFactory(mediaSourceFactory)
            .build()
    }

    var player: ExoPlayer = buildPlayer()
        private set

    val sessionPlayer: SessionPlayerWrapper = SessionPlayerWrapper(
        initialPlayer = player,
        onSkipNext = onSkipNext,
        onSkipPrevious = onSkipPrevious,
    )

    init {
        attachPlayerListeners(player)
    }

    private fun attachPlayerListeners(exoPlayer: ExoPlayer) {
        exoPlayer.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_ENDED) {
                    onEnded()
                }
                if (playbackState == Player.STATE_READY) {
                    onProgressTick()
                }
            }

            override fun onIsPlayingChanged(isPlaying: Boolean) {
                onIsPlayingChanged(isPlaying)
                onProgressTick()
            }

            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                onMediaItemTransition(mediaItem, reason)
                onProgressTick()
            }

            override fun onEvents(player: Player, events: Player.Events) {
                if (events.contains(Player.EVENT_PLAYBACK_STATE_CHANGED) ||
                    events.contains(Player.EVENT_PLAYBACK_PARAMETERS_CHANGED) ||
                    events.contains(Player.EVENT_TIMELINE_CHANGED) ||
                    events.contains(Player.EVENT_MEDIA_ITEM_TRANSITION) ||
                    events.contains(Player.EVENT_MEDIA_METADATA_CHANGED) ||
                    events.contains(Player.EVENT_POSITION_DISCONTINUITY)
                ) {
                    onProgressTick()
                }
            }

            override fun onPlayerError(error: androidx.media3.common.PlaybackException) {
                val httpCode = (error.cause as? androidx.media3.datasource.HttpDataSource.InvalidResponseCodeException)
                    ?.responseCode
                val message = if (httpCode != null) "http_$httpCode" else (error.message ?: "playback_error")
                onError(message)
            }
        })
    }

    fun setServerOrigin(origin: String) {
        serverOrigin = origin
        refreshDataSource()
    }

    fun refreshCookies() {
        CookieManager.getInstance().flush()
        refreshDataSource()
    }

    private fun refreshDataSource() {
        dataSourceFactory = CookieAwareDataSource(appContext) {
            serverOrigin.ifBlank { "http://127.0.0.1" }
        }
        val oldPlayer = player
        val position = oldPlayer.currentPosition
        val wasPlaying = oldPlayer.isPlaying
        val items = (0 until oldPlayer.mediaItemCount).map { oldPlayer.getMediaItemAt(it) }
        val index = oldPlayer.currentMediaItemIndex
        val newPlayer = buildPlayer()
        attachPlayerListeners(newPlayer)
        if (items.isNotEmpty()) {
            newPlayer.setMediaItems(items, index.coerceAtLeast(0), position)
            newPlayer.prepare()
            newPlayer.playWhenReady = wasPlaying
        }
        player = newPlayer
        sessionPlayer.bindPlayer(newPlayer)
        oldPlayer.release()
    }

    fun buildMediaItem(track: TrackMeta, artworkBytes: ByteArray? = null): MediaItem {
        val metadataBuilder = MediaMetadata.Builder()
            .setTitle(track.title)
            .setArtist(track.artist)
            .setAlbumTitle(track.album)
        if (artworkBytes != null && artworkBytes.isNotEmpty()) {
            metadataBuilder.setArtworkData(artworkBytes, MediaMetadata.PICTURE_TYPE_FRONT_COVER)
        } else if (track.artworkUri.isNotBlank()) {
            metadataBuilder.setArtworkUri(Uri.parse(track.artworkUri))
        }
        val builder = MediaItem.Builder()
            .setMediaId(track.filename)
            .setMediaMetadata(metadataBuilder.build())
        if (track.audioUrl.isNotBlank()) {
            builder.setUri(Uri.parse(track.audioUrl))
        }
        return builder.build()
    }

    fun setQueue(
        items: List<MediaItem>,
        startIndex: Int,
        startPositionMs: Long,
        autoPlay: Boolean,
        repeatSingle: Boolean
    ) {
        player.repeatMode = if (repeatSingle) Player.REPEAT_MODE_ONE else Player.REPEAT_MODE_OFF
        player.setMediaItems(items, startIndex.coerceIn(0, (items.size - 1).coerceAtLeast(0)), startPositionMs)
        player.prepare()
        player.playWhenReady = autoPlay
        items.getOrNull(startIndex.coerceIn(0, (items.size - 1).coerceAtLeast(0)))?.let { item ->
            currentTrack = trackFromMediaItem(item)
        }
    }

    fun updateItemAt(index: Int, item: MediaItem) {
        if (index < 0 || index >= player.mediaItemCount) return
        player.replaceMediaItem(index, item)
        if (player.currentMediaItemIndex == index) {
            item.mediaId?.let { currentTrack = trackFromMediaItem(item) }
        }
    }

    fun currentIndex(): Int = player.currentMediaItemIndex

    fun indexOfMediaId(filename: String): Int {
        if (filename.isBlank()) return -1
        for (i in 0 until player.mediaItemCount) {
            if (player.getMediaItemAt(i).mediaId == filename) return i
        }
        return -1
    }

    fun playTrack(
        track: TrackMeta,
        startSec: Double,
        autoPlay: Boolean,
        repeatSingle: Boolean
    ): Boolean {
        if (track.audioUrl.isBlank()) {
            onError("missing_audio_url")
            return false
        }
        currentTrack = track
        refreshCookies()
        val mediaItem = buildMediaItem(track)
        setQueue(listOf(mediaItem), 0, (startSec.coerceAtLeast(0.0) * 1000).toLong(), autoPlay, repeatSingle)
        loadArtworkForTrack(track, 0)
        return true
    }

    fun loadArtworkForTrack(track: TrackMeta, index: Int) {
        if (track.artworkUri.isBlank()) return
        artworkCache.loadArtworkBytes(track.artworkUri, serverOrigin) { bytes ->
            mainHandler.post {
                applyArtworkBytes(track, index, bytes)
            }
        }
    }

    private fun applyArtworkBytes(track: TrackMeta, index: Int, bytes: ByteArray?) {
        if (bytes == null || index >= player.mediaItemCount) return
        val existing = player.getMediaItemAt(index)
        if (existing.mediaId != track.filename) return
        val updated = buildMediaItem(track, bytes)
        updateItemAt(index, updated)
    }

    fun updateAudioUrl(track: TrackMeta) {
        currentTrack = track
        val position = player.currentPosition
        val autoPlay = player.isPlaying
        val repeatMode = player.repeatMode
        val index = currentIndex().coerceAtLeast(0)
        val item = buildMediaItem(track)
        if (player.mediaItemCount > 0) {
            updateItemAt(index, item)
            player.seekTo(position)
            player.playWhenReady = autoPlay
            loadArtworkForTrack(track, index)
        } else {
            playTrack(track, position / 1000.0, autoPlay, repeatMode == Player.REPEAT_MODE_ONE)
        }
    }

    fun pause() {
        player.playWhenReady = false
    }

    fun play() {
        player.playWhenReady = true
    }

    fun seekToSec(sec: Double) {
        player.seekTo((sec.coerceAtLeast(0.0) * 1000).toLong())
    }

    fun seekToMediaIndex(index: Int, autoPlay: Boolean = true) {
        if (index < 0 || index >= player.mediaItemCount) return
        player.seekToDefaultPosition(index)
        player.playWhenReady = autoPlay
    }

    fun currentSnapshot(queue: QueueEngine): PlaybackSnapshot {
        val durationSec = if (player.duration > 0) player.duration / 1000.0 else 0.0
        val positionSec = if (player.currentPosition >= 0) player.currentPosition / 1000.0 else 0.0
        val mediaId = player.currentMediaItem?.mediaId
        val track = if (!mediaId.isNullOrBlank()) {
            queue.trackFor(mediaId) ?: currentTrack.copy(filename = mediaId)
        } else {
            currentTrack
        }
        return PlaybackSnapshot(
            filename = queue.currentFilename.ifBlank { track.filename },
            currentTime = positionSec,
            duration = durationSec,
            isPlaying = player.isPlaying,
            playbackMode = queue.playbackMode,
            queueIds = queue.queueIds,
            shuffle = ShuffleState(
                pool = queue.shufflePool.toList(),
                history = queue.shuffleHistory.toList(),
                signature = queue.shuffleSignature
            ),
            track = track
        )
    }

    fun release() {
        sessionPlayer.release()
    }

    private fun trackFromMediaItem(item: MediaItem): TrackMeta {
        val meta = item.mediaMetadata
        return TrackMeta(
            filename = item.mediaId.orEmpty(),
            title = meta.title?.toString().orEmpty(),
            artist = meta.artist?.toString().orEmpty(),
            album = meta.albumTitle?.toString().orEmpty()
        )
    }
}
