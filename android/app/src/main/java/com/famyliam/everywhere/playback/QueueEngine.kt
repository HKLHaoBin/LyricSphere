package com.famyliam.everywhere.playback

import kotlin.random.Random

/**
 * Mirrors web pickNextQueueFilename / pickPrevQueueFilename.
 * Rebuilds shuffle pool locally when empty (Fisher–Yates), matching web.
 */
class QueueEngine {
    var queueIds: List<String> = emptyList()
        private set
    var playbackMode: String = "list"
        private set
    var currentFilename: String = ""
        private set
    var shufflePool: MutableList<String> = mutableListOf()
        private set
    var shuffleHistory: MutableList<String> = mutableListOf()
        private set
    var shuffleSignature: String = ""
        private set

    private val trackCache = linkedMapOf<String, TrackMeta>()

    fun updateFromPayload(payload: HandoffPayload) {
        queueIds = normalizeIds(payload.queueIds)
        playbackMode = payload.playbackMode
        currentFilename = payload.filename
        shufflePool = payload.shuffle.pool.toMutableList()
        shuffleHistory = payload.shuffle.history.toMutableList()
        shuffleSignature = payload.shuffle.signature
        cacheTracksFromPayload(payload)
    }

    fun syncQueue(payload: HandoffPayload) {
        queueIds = normalizeIds(payload.queueIds)
        playbackMode = payload.playbackMode
        if (payload.filename.isNotBlank()) {
            currentFilename = payload.filename
        }
        shufflePool = payload.shuffle.pool.toMutableList()
        shuffleHistory = payload.shuffle.history.toMutableList()
        shuffleSignature = payload.shuffle.signature
        cacheTracksFromPayload(payload)
    }

    private fun cacheTracksFromPayload(payload: HandoffPayload) {
        cacheTrack(payload.filename, payload.track)
        payload.upcomingTracks.forEach { cacheTrack(it.filename, it) }
    }

    fun cacheTrack(filename: String, meta: TrackMeta) {
        if (filename.isBlank()) return
        val prior = trackCache[filename]
        trackCache[filename] = if (prior != null) {
            meta.copy(
                audioUrl = meta.audioUrl.ifBlank { prior.audioUrl },
                title = meta.title.ifBlank { prior.title },
                artist = meta.artist.ifBlank { prior.artist },
                album = meta.album.ifBlank { prior.album },
                artworkUri = meta.artworkUri.ifBlank { prior.artworkUri }
            )
        } else {
            meta
        }
    }

    fun trackFor(filename: String): TrackMeta? = trackCache[filename]

    /** Cached payload audio URL; used only when /song-info fetch fails. */
    fun trackWithAudioUrl(filename: String): TrackMeta? {
        val meta = trackCache[filename] ?: return null
        return meta.takeIf { it.audioUrl.isNotBlank() }
    }

    fun pickNext(): String? {
        val ids = normalizeIds(queueIds)
        if (ids.isEmpty()) return null
        val cur = currentFilename.takeIf { it.isNotBlank() }
        return when (playbackMode) {
            "shuffle" -> pickNextShuffle(ids, cur)
            "single" -> cur ?: ids.firstOrNull()
            else -> pickNextList(ids, cur)
        }
    }

    fun pickPrev(): String? {
        val ids = normalizeIds(queueIds)
        if (ids.isEmpty()) return null
        val cur = currentFilename.takeIf { it.isNotBlank() }
        return when (playbackMode) {
            "shuffle" -> pickPrevShuffle(cur)
            else -> pickPrevList(ids, cur)
        }
    }

    fun onTrackStarted(filename: String) {
        currentFilename = filename
    }

    fun recordShuffleHistoryBeforeAdvance() {
        if (playbackMode != "shuffle") return
        val cur = currentFilename
        if (cur.isBlank()) return
        if (shuffleHistory.isEmpty() || shuffleHistory.last() != cur) {
            shuffleHistory.add(cur)
        }
    }

    /**
     * Advance after track ended; updates shuffle history/pool like web.
     * @return next filename and cached meta when available; null when queue cannot advance
     */
    fun onTrackEndedAdvance(): QueueAdvanceResult? {
        val ids = normalizeIds(queueIds)
        if (ids.isEmpty()) return null
        val cur = currentFilename

        if (playbackMode == "single") {
            if (cur.isBlank()) return null
            return QueueAdvanceResult(cur, trackCache[cur] ?: TrackMeta(cur))
        }

        if (playbackMode == "shuffle" && cur.isNotBlank()) {
            if (shuffleHistory.isEmpty() || shuffleHistory.last() != cur) {
                shuffleHistory.add(cur)
            }
        }

        val nextFilename = pickNext() ?: return null
        if (nextFilename == cur) return null
        currentFilename = nextFilename
        return QueueAdvanceResult(nextFilename, trackFor(nextFilename))
    }

    private fun pickNextList(ids: List<String>, cur: String?): String {
        var idx = if (!cur.isNullOrBlank()) ids.indexOf(cur) else 0
        if (idx < 0) idx = 0
        return ids[(idx + 1) % ids.size]
    }

    private fun pickPrevList(ids: List<String>, cur: String?): String {
        var idx = if (!cur.isNullOrBlank()) ids.indexOf(cur) else 0
        if (idx < 0) idx = 0
        return ids[(idx - 1 + ids.size) % ids.size]
    }

    private fun pickNextShuffle(ids: List<String>, cur: String?): String? {
        if (ids.size <= 1) return cur
        if (shufflePool.isEmpty()) {
            shufflePool = buildShuffleQueueIds(ids.filter { it != cur })
        }
        return shufflePool.removeFirstOrNull()
    }

    private fun pickPrevShuffle(cur: String?): String? {
        if (shuffleHistory.isEmpty()) return cur
        val prev = shuffleHistory.removeLastOrNull()
        if (!prev.isNullOrBlank() && cur != null && cur.isNotBlank()) {
            shufflePool.add(0, cur)
        }
        return prev ?: cur
    }

    /** Fisher–Yates shuffle; mirrors web buildShuffleQueueIds. */
    private fun buildShuffleQueueIds(ids: List<String>): MutableList<String> {
        val shuffled = ids.toMutableList()
        for (i in shuffled.lastIndex downTo 1) {
            val j = Random.nextInt(i + 1)
            val temp = shuffled[i]
            shuffled[i] = shuffled[j]
            shuffled[j] = temp
        }
        return shuffled
    }

    private fun normalizeIds(ids: List<String>): List<String> {
        val seen = linkedSetOf<String>()
        ids.forEach { id ->
            val trimmed = id.trim()
            if (trimmed.isNotEmpty()) seen.add(trimmed)
        }
        return seen.toList()
    }
}
