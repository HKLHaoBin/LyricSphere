package com.famyliam.everywhere.playback

import androidx.media3.common.ForwardingSimpleBasePlayer
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture

/**
 * Stable [Player] for [androidx.media3.session.MediaSession] that survives ExoPlayer
 * replacement during cookie / data-source refresh. Skip commands route to [QueueEngine]
 * handlers instead of ExoPlayer playlist navigation.
 */
@OptIn(UnstableApi::class)
@UnstableApi
class SessionPlayerWrapper(
    initialPlayer: Player,
    private val onSkipNext: () -> Unit,
    private val onSkipPrevious: () -> Unit,
) : ForwardingSimpleBasePlayer(initialPlayer) {

    fun bindPlayer(player: Player) {
        setPlayer(player)
    }

    override fun handleSeek(
        mediaItemIndex: Int,
        positionMs: Long,
        @Player.Command seekCommand: Int
    ): ListenableFuture<*> {
        when (seekCommand) {
            Player.COMMAND_SEEK_TO_NEXT,
            Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM -> {
                onSkipNext()
                return Futures.immediateVoidFuture()
            }
            Player.COMMAND_SEEK_TO_PREVIOUS,
            Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM -> {
                onSkipPrevious()
                return Futures.immediateVoidFuture()
            }
        }
        return super.handleSeek(mediaItemIndex, positionMs, seekCommand)
    }
}
