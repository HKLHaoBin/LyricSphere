package com.famyliam.everywhere.util

import android.content.Context
import android.webkit.CookieManager
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executor

class ArtworkCache(
    private val context: Context,
    private val executor: Executor
) {
    private val cacheDir: File by lazy {
        File(context.cacheDir, "artwork").apply { mkdirs() }
    }

    fun loadArtworkBytes(
        artworkUri: String,
        serverOrigin: String,
        onResult: (ByteArray?) -> Unit
    ) {
        if (artworkUri.isBlank()) {
            onResult(null)
            return
        }
        val cacheKey = sha256(artworkUri)
        val cached = File(cacheDir, cacheKey)
        executor.execute {
            if (cached.isFile && cached.length() > 0) {
                onResult(cached.readBytes())
                return@execute
            }
            val bytes = downloadArtwork(artworkUri, serverOrigin)
            if (bytes != null && bytes.isNotEmpty()) {
                try {
                    cached.writeBytes(bytes)
                } catch (_: Exception) {
                    // ignore cache write failure
                }
            }
            onResult(bytes)
        }
    }

    private fun downloadArtwork(artworkUri: String, serverOrigin: String): ByteArray? {
        val resolved = SongInfoResolverCompat.resolveUrl(serverOrigin, artworkUri)
        if (resolved.isBlank()) return null
        return try {
            val connection = (URL(resolved).openConnection() as HttpURLConnection).apply {
                connectTimeout = 10000
                readTimeout = 10000
                if (serverOrigin.isNotBlank()) {
                    val cookie = CookieManager.getInstance().getCookie(serverOrigin).orEmpty()
                    if (cookie.isNotBlank()) setRequestProperty("Cookie", cookie)
                }
            }
            val code = connection.responseCode
            val bytes = if (code in 200..299) {
                connection.inputStream.use { it.readBytes() }
            } else {
                null
            }
            connection.disconnect()
            bytes
        } catch (_: Exception) {
            null
        }
    }

    private fun sha256(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(value.toByteArray())
        return digest.joinToString("") { "%02x".format(it) }
    }

    /** Local URL resolver without depending on playback package from util layer. */
    private object SongInfoResolverCompat {
        fun resolveUrl(serverOrigin: String, value: String): String {
            if (value.isBlank()) return ""
            if (value.startsWith("http://") || value.startsWith("https://") || value.startsWith("data:")) {
                return value
            }
            if (serverOrigin.isBlank()) return value
            val path = if (value.startsWith("/")) value else "/$value"
            return "$serverOrigin$path"
        }
    }
}
