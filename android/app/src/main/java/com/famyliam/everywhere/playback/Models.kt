package com.famyliam.everywhere.playback



import org.json.JSONArray

import org.json.JSONObject



data class TrackMeta(

    val filename: String,

    val title: String = "",

    val artist: String = "",

    val album: String = "",

    val artworkUri: String = "",

    val audioUrl: String = ""

)



data class ShuffleState(

    val pool: List<String> = emptyList(),

    val history: List<String> = emptyList(),

    val signature: String = ""

)



/** Explicit next-track result from queue advance; null means no advance. */

data class QueueAdvanceResult(

    val filename: String,

    val meta: TrackMeta? = null

)



data class PlaybackSnapshot(

    val filename: String = "",

    val currentTime: Double = 0.0,

    val duration: Double = 0.0,

    val isPlaying: Boolean = false,

    val playbackMode: String = "list",

    val queueIds: List<String> = emptyList(),

    val shuffle: ShuffleState = ShuffleState(),

    val track: TrackMeta = TrackMeta(""),

    val amllMode: String = "local",

    val error: String? = null

)



data class HandoffPayload(

    val filename: String,

    val currentTime: Double,

    val duration: Double,

    val isPlaying: Boolean,

    val playbackMode: String,

    val queueIds: List<String>,

    val shuffle: ShuffleState,

    val track: TrackMeta,

    val amllMode: String,

    val upcomingTracks: List<TrackMeta> = emptyList()

)



object PayloadJson {

    fun parseHandoff(json: String): HandoffPayload? {

        return try {

            val root = JSONObject(json)

            val filename = root.optString("filename")

            val track = parseCurrentTrack(root, filename)

            HandoffPayload(

                filename = filename,

                currentTime = root.optDouble("currentTime", 0.0),

                duration = root.optDouble("duration", 0.0),

                isPlaying = root.optBoolean("isPlaying", false),

                playbackMode = root.optString("playbackMode", "list"),

                queueIds = parseStringArray(root.optJSONArray("queueIds")),

                shuffle = parseShuffle(root),

                track = track,

                amllMode = root.optString("amllMode", "local"),

                upcomingTracks = parseUpcomingTracks(root)

            )

        } catch (_: Exception) {

            null

        }

    }



    fun snapshotToEventJson(snapshot: PlaybackSnapshot): String {

        val shuffle = JSONObject()

            .put("pool", JSONArray(snapshot.shuffle.pool))

            .put("history", JSONArray(snapshot.shuffle.history))

            .put("signature", snapshot.shuffle.signature)

        val detail = JSONObject()

            .put("filename", snapshot.filename)

            .put("currentTime", snapshot.currentTime)

            .put("duration", snapshot.duration)

            .put("isPlaying", snapshot.isPlaying)

            .put("playbackMode", snapshot.playbackMode)

            .put("queueIds", JSONArray(snapshot.queueIds))

            .put("shuffle", shuffle)

        snapshot.error?.let { detail.put("error", it) }

        return detail.toString()

    }



    /** Current track: nested `track` when present, merged with top-level flat Web fields. */

    private fun parseCurrentTrack(root: JSONObject, filename: String): TrackMeta {

        val trackObj = root.optJSONObject("track")

        val track = if (trackObj != null) {

            parseTrackMeta(trackObj, filename.ifBlank { trackObj.optString("filename") })

        } else {

            TrackMeta(

                filename = filename,

                title = root.optString("title"),

                artist = root.optString("artist"),

                album = root.optString("album"),

                artworkUri = root.optString("artworkUri"),

                audioUrl = root.optString("audioUrl")

            )

        }

        return track.copy(

            filename = track.filename.ifBlank { filename },

            title = track.title.ifBlank { root.optString("title") },

            artist = track.artist.ifBlank { root.optString("artist") },

            album = track.album.ifBlank { root.optString("album") },

            artworkUri = track.artworkUri.ifBlank { root.optString("artworkUri") },

            audioUrl = track.audioUrl.ifBlank { root.optString("audioUrl") }

        )

    }



    private fun parseShuffle(root: JSONObject): ShuffleState {

        val shuffleObj = root.optJSONObject("shuffle")

        if (shuffleObj != null) {

            return ShuffleState(

                pool = parseStringArray(shuffleObj.optJSONArray("pool")),

                history = parseStringArray(shuffleObj.optJSONArray("history")),

                signature = shuffleObj.optString("signature", "")

            )

        }

        return ShuffleState(

            pool = parseStringArray(root.optJSONArray("shufflePool")),

            history = parseStringArray(root.optJSONArray("shuffleHistory")),

            signature = root.optString("shuffleSignature", "")

        )

    }



    /** Web `tracks[]` or optional `upcomingTracks[]`. */

    private fun parseUpcomingTracks(root: JSONObject): List<TrackMeta> {

        val fromTracks = parseUpcoming(root.optJSONArray("tracks"))

        if (fromTracks.isNotEmpty()) return fromTracks

        return parseUpcoming(root.optJSONArray("upcomingTracks"))

    }



    private fun parseTrackMeta(item: JSONObject, fallbackFilename: String): TrackMeta {

        val filename = item.optString("filename").ifBlank { fallbackFilename }

        return TrackMeta(

            filename = filename,

            title = item.optString("title"),

            artist = item.optString("artist"),

            album = item.optString("album"),

            artworkUri = item.optString("artworkUri"),

            audioUrl = item.optString("audioUrl")

        )

    }



    private fun parseUpcoming(array: JSONArray?): List<TrackMeta> {

        if (array == null) return emptyList()

        val result = mutableListOf<TrackMeta>()

        for (i in 0 until array.length()) {

            val item = array.optJSONObject(i) ?: continue

            val filename = item.optString("filename")

            if (filename.isBlank()) continue

            result.add(parseTrackMeta(item, filename))

        }

        return result

    }



    private fun parseStringArray(array: JSONArray?): List<String> {

        if (array == null) return emptyList()

        val result = mutableListOf<String>()

        for (i in 0 until array.length()) {

            val value = array.optString(i).trim()

            if (value.isNotEmpty()) result.add(value)

        }

        return result

    }

}

/** Parses GET /song-info single-object JSON; resolves media URLs against server origin. */
object SongInfoResolver {
    fun parseResponse(filename: String, body: String, serverOrigin: String): TrackMeta? {
        return try {
            val root = JSONObject(body)
            if (root.has("error")) return null

            val resolvedFilename = root.optString("filename").ifBlank { filename }
            val metaObj = root.optJSONObject("meta")
            val artistsArray = root.optJSONArray("artists")
            val artists = buildList {
                if (artistsArray != null) {
                    for (i in 0 until artistsArray.length()) add(artistsArray.optString(i))
                }
            }.filter { it.isNotBlank() }.joinToString(", ")

            val album = root.optString("album").ifBlank {
                metaObj?.optString("album").orEmpty()
            }
            val artworkRaw = root.optString("albumImgSrc").ifBlank {
                metaObj?.optString("albumImgSrc").orEmpty()
            }.ifBlank {
                root.optString("cover")
            }
            val audioDelivery = root.optJSONObject("audio_delivery")
            val rawAudio = root.optString("song").ifBlank {
                audioDelivery?.optString("url").orEmpty()
            }
            val audioUrl = resolveMediaUrl(serverOrigin, rawAudio)
            if (audioUrl.isBlank()) return null

            TrackMeta(
                filename = resolvedFilename,
                title = root.optString("title"),
                artist = artists,
                album = album,
                artworkUri = resolveMediaUrl(serverOrigin, artworkRaw),
                audioUrl = audioUrl
            )
        } catch (_: Exception) {
            null
        }
    }

    fun mergeWithCache(fresh: TrackMeta, cached: TrackMeta?): TrackMeta {
        if (cached == null) return fresh
        return fresh.copy(
            title = fresh.title.ifBlank { cached.title },
            artist = fresh.artist.ifBlank { cached.artist },
            album = fresh.album.ifBlank { cached.album },
            artworkUri = fresh.artworkUri.ifBlank { cached.artworkUri }
        )
    }

    fun resolveMediaUrl(serverOrigin: String, value: String): String {
        if (value.isBlank()) return ""
        if (value.startsWith("http://") || value.startsWith("https://") || value.startsWith("data:")) {
            return value
        }
        if (serverOrigin.isBlank()) return value
        val path = if (value.startsWith("/")) value else "/$value"
        return "$serverOrigin$path"
    }
}


