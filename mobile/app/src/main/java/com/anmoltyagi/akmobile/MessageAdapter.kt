package com.anmoltyagi.akmobile

import android.view.LayoutInflater
import android.view.ViewGroup
import android.view.animation.AnimationUtils
import androidx.recyclerview.widget.RecyclerView
import com.anmoltyagi.akmobile.databinding.ItemMessageBinding

class MessageAdapter(
    private val items: MutableList<Message>
) : RecyclerView.Adapter<MessageAdapter.VH>() {

    class VH(val binding: ItemMessageBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemMessageBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return VH(binding)
    }

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        val msg = items[position]
        val b = holder.binding
        b.text.text = msg.text
        b.userText.text = msg.text
        b.time.text = msg.time
        b.userTime.text = msg.time

        if (msg.fromUser) {
            b.userBubble.visibility = android.view.View.VISIBLE
            b.akBubble.visibility = android.view.View.GONE
        } else {
            b.akBubble.visibility = android.view.View.VISIBLE
            b.userBubble.visibility = android.view.View.GONE
        }

        if (position == items.size - 1 && items.size > 1) {
            holder.itemView.startAnimation(
                AnimationUtils.loadAnimation(b.root.context, R.anim.msg_in)
            )
        }
    }

    fun add(msg: Message) {
        items.add(msg)
        notifyItemInserted(items.size - 1)
    }
}