package com.famyliam.everywhere.playback

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * Persists a recoverable playback snapshot to SharedPreferences with throttled writes.
 */
class PlaybackStateStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private var lastWriteAtMs: Long = 0L

    data class PersistedSnapshot(
        val serverOrigin: String = "",
        val filename: String = "",
        val currentTime: Double = 0.0,
        val duration: Double = 0.0,
        val isPlaying: Boolean = false,
        val playbackMode: String = "list",
        val queueIds: List<String> = emptyList(),
        val shuffle: ShuffleState = ShuffleState(),
        val nativeActive: Boolean = false,
        val amllMode: String = "local",
        val trackCache: Map<String, TrackMeta> = emptyMap()
    )

    fun saveThrottled(snapshot: PersistedSnapshot, throttleMs: Long = 3000L, force: Boolean = false) {
        val now = System.currentTimeMillis()
        if (!force && now - lastWriteAtMs < throttleMs) return
        lastWriteAtMs = now
        save(snapshot)
    }

    fun save(snapshot: PersistedSnapshot) {
        prefs.edit()
            .putString(KEY_JSON, snapshotToJson(snapshot).toString())
            .apply()
        lastWriteAtMs = System.currentTimeMillis()
    }

    fun load(): PersistedSnapshot? {
        val raw = prefs.getString(KEY_JSON, null) ?: return null
        return parseJson(raw)
    }

    fun clear() {
        prefs.edit().remove(KEY_JSON).apply()
    }

    fun hasRecoverableState(): Boolean {
        val snap = load() ?: return false
        return snap.nativeActive ||
            snap.queueIds.isNotEmpty() ||
            snap.isPlaying ||
            snap.filename.isNotBlank()
    }

    companion object {
        private const val PREFS_NAME = "famyliam_playback_state"
        private const val KEY_JSON = "snapshot_json"

        fun fromRuntime(
            serverOrigin: String,
            queue: QueueEngine,
            controller: PlaybackController?,
            nativeActive: Boolean,
            amllMode: String
        ): PersistedSnapshot {
            val playback = controller?.currentSnapshot(queue) ?: PlaybackSnapshot()
            val slimCache = linkedMapOf<String, TrackMeta>()
            val seen = linkedSetOf<String>()
            queue.queueIds.forEach { id -> seen.add(id) }
            if (queue.currentFilename.isNotBlank()) seen.add(queue.currentFilename)
            seen.forEach { id ->
                queue.trackFor(id)?.let { track ->
                    slimCache[id] = track.copy(audioUrl = "")
                }
            }
            return PersistedSnapshot(
                serverOrigin = serverOrigin,
                filename = playback.filename,
                currentTime = playback.currentTime,
                duration = playback.duration,
                isPlaying = playback.isPlaying,
                playbackMode = playback.playbackMode,
                queueIds = playback.queueIds,
                shuffle = playback.shuffle,
                nativeActive = nativeActive,
                amllMode = amllMode,
                trackCache = slimCache
            )
        }

        private fun snapshotToJson(snapshot: PersistedSnapshot): JSONObject {
            val cacheArray = JSONArray()
            snapshot.trackCache.forEach { (filename, track) ->
                cacheArray.put(
                    JSONObject()
                        .put("filename", filename)
                        .put("title", track.title)
                        .put("artist", track.artist)
                        .put("album", track.album)
                        .put("artworkUri", track.artworkUri)
                )
            }
            val shuffle = JSONObject()
                .put("pool", JSONArray(snapshot.shuffle.pool))
                .put("history", JSONArray(snapshot.shuffle.history))
                .put("signature", snapshot.shuffle.signature)
            return JSONObject()
                .put("serverOrigin", snapshot.serverOrigin)
                .put("filename", snapshot.filename)
                .put("currentTime", snapshot.currentTime)
                .put("duration", snapshot.duration)
                .put("isPlaying", snapshot.isPlaying)
                .put("playbackMode", snapshot.playbackMode)
                .put("queueIds", JSONArray(snapshot.queueIds))
                .put("shuffle", shuffle)
                .put("nativeActive", snapshot.nativeActive)
                .put("amllMode", snapshot.amllMode)
                .put("trackCache", cacheArray)
        }

        private fun parseJson(raw: String): PersistedSnapshot? {
            return try {
                val root = JSONObject(raw)
                val cache = linkedMapOf<String, TrackMeta>()
                root.optJSONArray("trackCache")?.let { array ->
                    for (i in 0 until array.length()) {
                        val item = array.optJSONObject(i) ?: continue
                        val filename = item.optString("filename")
                        if (filename.isBlank()) continue
                        cache[filename] = TrackMeta(
                            filename = filename,
                            title = item.optString("title"),
                            artist = item.optString("artist"),
                            album = item.optString("album"),
                            artworkUri = item.optString("artworkUri")
                        )
                    }
                }
                val shuffleObj = root.optJSONObject("shuffle")
                val shuffle = if (shuffleObj != null) {
                    ShuffleState(
                        pool = parseStringArray(shuffleObj.optJSONArray("pool")),
                        history = parseStringArray(shuffleObj.optJSONArray("history")),
                        signature = shuffleObj.optString("signature", "")
                    )
                } else {
                    ShuffleState()
                }
                PersistedSnapshot(
                    serverOrigin = root.optString("serverOrigin"),
                    filename = root.optString("filename"),
                    currentTime = root.optDouble("currentTime", 0.0),
                    duration = root.optDouble("duration", 0.0),
                    isPlaying = root.optBoolean("isPlaying", false),
                    playbackMode = root.optString("playbackMode", "list"),
                    queueIds = parseStringArray(root.optJSONArray("queueIds")),
                    shuffle = shuffle,
                    nativeActive = root.optBoolean("nativeActive", false),
                    amllMode = root.optString("amllMode", "local"),
                    trackCache = cache
                )
            } catch (_: Exception) {
                null
            }
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
}
