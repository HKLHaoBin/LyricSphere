package com.famyliam.everywhere.util

import java.net.URI

object ServerUrlNormalizer {
    private const val PLAYER_PATH = "/lyric-sphere-v2"

    /**
     * Trim trailing slash; append /lyric-sphere-v2 when path does not already contain it.
     */
    fun normalize(input: String): String {
        var base = input.trim().trimEnd('/')
        if (base.isEmpty()) return base
        if (!base.startsWith("http://") && !base.startsWith("https://")) {
            base = "http://$base"
        }
        if (!base.contains(PLAYER_PATH)) {
            base = "$base$PLAYER_PATH"
        }
        return base
    }

    fun extractOrigin(serverUrl: String): String {
        return try {
            val uri = URI(serverUrl)
            val scheme = uri.scheme ?: "http"
            val host = uri.host ?: return serverUrl
            val port = uri.port
            if (port > 0 && port != 80 && port != 443) {
                "$scheme://$host:$port"
            } else {
                "$scheme://$host"
            }
        } catch (_: Exception) {
            serverUrl
        }
    }
}
