package com.famyliam.everywhere.playback

import android.content.Context
import android.net.Uri
import android.webkit.CookieManager
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.famyliam.everywhere.util.CookieAwareDataSource

class PlaybackController(
    context: Context,
    private val onEnded: () -> Unit,
    private val onError: (String) -> Unit,
    private val onIsPlayingChanged: (Boolean) -> Unit
) {
    private val appContext = context.applicationContext
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

    init {
        attachPlayerListeners(player)
    }

    private fun attachPlayerListeners(exoPlayer: ExoPlayer) {
        exoPlayer.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_ENDED) {
                    onEnded()
                }
            }

            override fun onIsPlayingChanged(isPlaying: Boolean) {
                onIsPlayingChanged(isPlaying)
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
        val position = player.currentPosition
        val wasPlaying = player.isPlaying
        val mediaItem = player.currentMediaItem
        player.release()
        player = buildPlayer()
        attachPlayerListeners(player)
        if (mediaItem != null) {
            player.setMediaItem(mediaItem)
            player.prepare()
            player.seekTo(position)
            player.playWhenReady = wasPlaying
        }
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

        player.repeatMode = if (repeatSingle) Player.REPEAT_MODE_ONE else Player.REPEAT_MODE_OFF
        val mediaItem = MediaItem.Builder()
            .setUri(Uri.parse(track.audioUrl))
            .setMediaId(track.filename)
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(track.title)
                    .setArtist(track.artist)
                    .setAlbumTitle(track.album)
                    .setArtworkUri(track.artworkUri.takeIf { it.isNotBlank() }?.let(Uri::parse))
                    .build()
            )
            .build()

        player.setMediaItem(mediaItem)
        player.prepare()
        val startMs = (startSec.coerceAtLeast(0.0) * 1000).toLong()
        player.seekTo(startMs)
        player.playWhenReady = autoPlay
        return true
    }

    fun updateAudioUrl(track: TrackMeta) {
        currentTrack = track
        val position = player.currentPosition
        val autoPlay = player.isPlaying
        val repeatMode = player.repeatMode
        playTrack(track, position / 1000.0, autoPlay, repeatMode == Player.REPEAT_MODE_ONE)
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

    fun currentSnapshot(queue: QueueEngine): PlaybackSnapshot {
        val durationSec = if (player.duration > 0) player.duration / 1000.0 else 0.0
        val positionSec = if (player.currentPosition >= 0) player.currentPosition / 1000.0 else 0.0
        return PlaybackSnapshot(
            filename = queue.currentFilename.ifBlank { currentTrack.filename },
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
            track = currentTrack
        )
    }

    fun release() {
        player.release()
    }
}
