package io.github.ninjatuna007.henchline

import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * A Quick Settings tile: call your crew, or hang up. Android never adds a tile by itself: it is there to add
 * from the Quick Settings editor if you want it (the user's wish, 2026-11-15).
 */
class CallTile : TileService() {

    override fun onStartListening() {
        super.onStartListening()
        val on = CallService.Bus.service != null
        qsTile?.apply {
            state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
            label = if (on) "Hang up" else "Call your crew"
            updateTile()
        }
    }

    override fun onClick() {
        super.onClick()
        if (CallService.Bus.service != null) {
            startService(Intent(this, CallService::class.java).setAction(CallService.STOP))
            qsTile?.apply { state = Tile.STATE_INACTIVE; label = "Call your crew"; updateTile() }
            return
        }
        // The call needs the microphone, which a service started from the background may not have: the app
        // opens and calls, as tapping its Call button would
        val call = Intent(this, MainActivity::class.java).setAction(MainActivity.CALL_NOW)
            .putExtra(MainActivity.CALL_KEY, MainActivity.callKey(this)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(PendingIntent.getActivity(this, 0, call, PendingIntent.FLAG_IMMUTABLE))
        } else {
            @Suppress("DEPRECATION")
            startActivityAndCollapse(call)
        }
    }
}
