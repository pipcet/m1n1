#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import sys

from m1n1.proxy import *
from .context import *
from .event import GPUEventManager
from m1n1.constructutils import ConstructClass

ASAHI_ATTACHMENT_C  = 0
ASAHI_ATTACHMENT_Z  = 1
ASAHI_ATTACHMENT_S  = 2

def unswizzle(agx, addr, w, h, psize, dump=None, grid=False):
    iface = agx.u.iface

    tw = 64
    th = 64
    ntx = (w + tw - 1) // 64
    nty = (h + th - 1) // 64
    data = iface.readmem(addr, ntx * nty * psize * tw * th)
    new_data = []
    for y in range(h):
        ty = y // th
        for x in range(w):
            tx = x // tw
            toff = tw * th * psize * (ty * ntx + tx)
            j = x & (tw - 1)
            i = y & (th - 1)
            off = (
                ((j & 1) << 0) | ((i & 1) << 1) |
                ((j & 2) << 1) | ((i & 2) << 2) |
                ((j & 4) << 2) | ((i & 4) << 3) |
                ((j & 8) << 3) | ((i & 8) << 4) |
                ((j & 16) << 4) | ((i & 16) << 5) |
                ((j & 32) << 5) | ((i & 32) << 6))
            r,g,b,a = data[toff + psize*off: toff + psize*(off+1)]
            if grid:
                if x % 64 == 0 or y % 64 == 0:
                    r,g,b,a = 255,255,255,255
                elif x % 32 == 0 or y % 32 == 0:
                    r,g,b,a = 128,128,128,255
            new_data.append(bytes([b, g, r, a]))
    data = b"".join(new_data)
    if dump:
        open(dump, "wb").write(data[:w*h*psize])
    #iface.writemem(addr, data)


class GPURenderer(object):
    def __init__(self, agx):
        self.agx = agx
        self.ctx_id = 3
        self.ctx_something = 2

        self.ctx = ctx = GPUContext(agx)
        self.ctx.bind(self.ctx_id)

        self.buffer_mgr = GPUBufferManager(agx, ctx, 16)
        self.buffer_mgr_initialized = False

        self.wq_3d = GPU3DWorkQueue(agx, ctx)
        self.wq_ta = GPUTAWorkQueue(agx, ctx)

        self.stamp_value = 0

        ##### TA stamps

        # start?
        self.stamp_ta1 = agx.kshared.new(BarrierCounter, name="TA stamp 1")
        self.stamp_ta1.value = self.stamp_value
        self.stamp_ta1.push()

        # complete?
        self.stamp_ta2 = agx.kobj.new(BarrierCounter, name="TA stamp 2")
        self.stamp_ta2.value = self.stamp_value
        self.stamp_ta2.push()

        ##### 3D stamps

        # start?
        self.stamp_3d1 = agx.kshared.new(BarrierCounter, name="3D stamp 1")
        self.stamp_3d1.value = self.stamp_value
        self.stamp_3d1.push()

        # complete?
        self.stamp_3d2 = agx.kobj.new(BarrierCounter, name="3D stamp 2")
        self.stamp_3d2.value = self.stamp_value
        self.stamp_3d2.push()


        ##### Things userspace deals with for macOS

        self.aux_fb = ctx.uobj.new_buf(0x8000, "Aux FB thing")
        #self.deflake_1 = ctx.uobj.new_buf(0x20, "Deflake 1")
        #self.deflake_2 = ctx.uobj.new_buf(0x280, "Deflake 2")
        #self.deflake_3 = ctx.uobj.new_buf(0x540, "Deflake 3")
        self.deflake = ctx.uobj.new_buf(0x7e0, "Deflake")
        self.unk_buf = ctx.uobj.new(Array(0x800, Int64ul), "Unknown Buffer")
        self.unk_buf.value = [0, *range(1, 0x400), *(0x400 * [0])]

        ##### Some kind of feedback/status buffer, GPU managed?

        self.event_control = agx.kobj.new(EventControl)
        self.event_control.event_count = agx.kobj.new(Int32ul, "event_control")
        self.event_control.event_count.val = 0
        self.event_control.event_count.push()

        self.event_control.base_stamp = 0
        self.event_control.unk_c = 0
        self.event_control.unk_10 = 0x50
        self.event_control.push()

        self.frames = 0
        self.rframes = 0

    def submit(self, cmdbuf):
        self.frames += 1

        ev_ta = self.agx.event_mgr.allocate_event()
        ev_3d = self.agx.event_mgr.allocate_event()

        deflake_1 = self.deflake._addr + 0x2a0
        deflake_2 = self.deflake._addr + 0x20
        deflake_3 = self.deflake._addr

        self.event_control.base_stamp = self.stamp_value >> 8
        self.event_control.push()

        self.stamp_value += 0x100
        self.event_control.event_count.val = 2
        self.event_control.event_count.push()

        agx = self.agx
        ctx = self.ctx

        width = cmdbuf.fb_width
        height = cmdbuf.fb_height

        ##### TVB allocations / Tiler config

        tile_width = 32
        tile_height = 32
        tiles_x = ((width + tile_width - 1) // tile_width)
        tiles_y = ((height + tile_height - 1) // tile_height)
        tiles = tiles_x * tiles_y

        tile_blocks_x = (tiles_x + 15) // 16
        tile_blocks_y = (tiles_y + 15) // 16
        tile_blocks = tile_blocks_x * tile_blocks_y

        tiling_params = TilingParameters()
        tiling_params.size1 = 0x14 * tile_blocks
        tiling_params.unk_4 = 0x88
        tiling_params.unk_8 = 0x202
        tiling_params.x_max = width - 1
        tiling_params.y_max = height - 1
        tiling_params.tile_count = ((tiles_y-1) << 12) | (tiles_x-1)
        tiling_params.x_blocks = (12 * tile_blocks_x) | (tile_blocks_x << 12) | (tile_blocks_x << 20)
        tiling_params.y_blocks = (12 * tile_blocks_y) | (tile_blocks_y << 12) | (tile_blocks_y << 20)
        tiling_params.size2 = 0x10 * tile_blocks
        tiling_params.size3 = 0x20 * tile_blocks
        tiling_params.unk_24 = 0x100
        tiling_params.unk_28 = 0x8000

        tvb_something_size = 0x800 * tile_blocks
        tvb_something = ctx.uobj.new_buf(tvb_something_size, "TVB Something")

        tvb_tilemap_size = 0x800 * tile_blocks
        tvb_tilemap = ctx.uobj.new_buf(tvb_tilemap_size, "TVB Tilemap")

        tvb_heapmeta_size = 0x4000
        tvb_heapmeta = ctx.uobj.new_buf(tvb_heapmeta_size, "TVB Heap Meta")

        ##### Buffer stuff?

        # buffer related?
        buf_desc = agx.kobj.new(BufferThing)
        buf_desc.unk_0 = 0x0
        buf_desc.unk_8 = 0x0
        buf_desc.unk_10 = 0x0
        buf_desc.unkptr_18 = ctx.uobj.buf(0x80, "BufferThing.unkptr_18")
        buf_desc.unk_20 = 0x0
        buf_desc.bm_misc_addr = self.buffer_mgr.misc_obj._addr
        buf_desc.unk_2c = 0x0
        buf_desc.unk_30 = 0x0
        buf_desc.unk_38 = 0x0
        buf_desc.push()

        uuid_3d = cmdbuf.cmd_3d_id
        uuid_ta = cmdbuf.cmd_ta_id
        encoder_id = cmdbuf.encoder_id

        ##### 3D barrier command

        barrier_cmd = agx.kobj.new(WorkCommandBarrier)
        barrier_cmd.stamp = self.stamp_ta2
        barrier_cmd.stamp_value1 = self.stamp_value
        barrier_cmd.stamp_value2 = self.stamp_value
        barrier_cmd.event = ev_ta.id
        barrier_cmd.uuid = uuid_3d


        #stamp.add_to_mon(mon)
        #stamp2.add_to_mon(mon)

        #print(barrier_cmd)

        self.wq_3d.submit(barrier_cmd)

        ##### 3D execution

        wc_3d = agx.kobj.new(WorkCommand3D)
        wc_3d.context_id = self.ctx_id
        wc_3d.unk_8 = 0
        wc_3d.event_control = self.event_control
        wc_3d.buffer_mgr = self.buffer_mgr.info
        wc_3d.buf_thing = buf_desc
        wc_3d.unk_emptybuf_addr = agx.kobj.buf(0x100, "unk_emptybuf")
        wc_3d.tvb_tilemap = tvb_tilemap._addr
        wc_3d.unk_40 = 0x88
        wc_3d.unk_48 = 0x1
        wc_3d.tile_blocks_y = tile_blocks_y * 4
        wc_3d.tile_blocks_x = tile_blocks_x * 4
        wc_3d.unk_50 = 0x0
        wc_3d.unk_58 = 0x0
        wc_3d.uuid1 = 0x3b315cae # ??
        wc_3d.uuid2 = 0x3b6c7b92 # ??
        wc_3d.unk_68 = 0x0
        wc_3d.tile_count = tiles

        wc_3d.unk_buf = WorkCommand1_UnkBuf()
        wc_3d.unk_word = BarrierCounter()
        wc_3d.unk_buf2 = WorkCommand1_UnkBuf2()
        wc_3d.unk_buf2.unk_0 = 0
        wc_3d.unk_buf2.unk_8 = 0
        wc_3d.unk_buf2.unk_10 = 1
        wc_3d.ts1 = Timestamp(agx.initdata.regionC._addr + 0x9058)
        wc_3d.ts2 = Timestamp(agx.initdata.regionC._addr + 0x9060)
        wc_3d.ts3 = Timestamp(agx.initdata.regionC._addr + 0x9068)
        wc_3d.unk_914 = 0
        wc_3d.unk_918 = 0
        wc_3d.unk_920 = 0
        wc_3d.unk_924 = 1

        # Structures embedded in WorkCommand3D
        if True:
            wc_3d.struct_1 = Start3DStruct1()
            wc_3d.struct_1.store_pipeline_addr = cmdbuf.store_pipeline | 4
            wc_3d.struct_1.unk_8 = 0x0
            wc_3d.struct_1.unk_c = 0x0
            wc_3d.struct_1.uuid1 = wc_3d.uuid1
            wc_3d.struct_1.uuid2 = wc_3d.uuid2
            wc_3d.struct_1.unk_18 = 0x0
            wc_3d.struct_1.tile_blocks_y = tile_blocks_y * 4
            wc_3d.struct_1.tile_blocks_x = tile_blocks_x * 4
            wc_3d.struct_1.unk_24 = 0x0
            wc_3d.struct_1.tile_counts = ((tiles_y-1) << 12) | (tiles_x-1)
            wc_3d.struct_1.unk_2c = 0x8
            wc_3d.struct_1.depth_clear_val1 = cmdbuf.depth_clear_value
            wc_3d.struct_1.stencil_clear_val1 = cmdbuf.stencil_clear_value
            wc_3d.struct_1.unk_38 = 0x0
            wc_3d.struct_1.unk_3c = 0x1
            wc_3d.struct_1.unk_40_padding = bytes(0xb0)
            wc_3d.struct_1.depth_bias_array = Start3DArrayAddr(cmdbuf.depth_bias_array)
            wc_3d.struct_1.scissor_array = Start3DArrayAddr(cmdbuf.scissor_array)
            wc_3d.struct_1.unk_110 = 0x0
            wc_3d.struct_1.unk_118 = 0x0
            wc_3d.struct_1.unk_120 = [0] * 37
            wc_3d.struct_1.unk_reload_pipeline = Start3DClearPipelineBinding(
                cmdbuf.partial_reload_pipeline_bind, cmdbuf.partial_reload_pipeline | 4)#Start3DStorePipelineBinding(0xffff8212, 0xfffffff4)
            wc_3d.struct_1.unk_258 = 0
            wc_3d.struct_1.unk_260 = 0
            wc_3d.struct_1.unk_268 = 0
            wc_3d.struct_1.unk_270 = 0
            wc_3d.struct_1.reload_pipeline = Start3DClearPipelineBinding(
                cmdbuf.partial_reload_pipeline_bind, cmdbuf.partial_reload_pipeline | 4)
            wc_3d.struct_1.depth_flags = cmdbuf.ds_flags
            wc_3d.struct_1.unk_290 = 0x0
            wc_3d.struct_1.depth_buffer_ptr1 = cmdbuf.depth_buffer
            wc_3d.struct_1.unk_2a0 = 0x0
            wc_3d.struct_1.unk_2a8 = 0x0
            wc_3d.struct_1.depth_buffer_ptr2 = cmdbuf.depth_buffer
            wc_3d.struct_1.depth_buffer_ptr3 = cmdbuf.depth_buffer
            wc_3d.struct_1.unk_2c0 = 0x0
            wc_3d.struct_1.stencil_buffer_ptr1 = cmdbuf.stencil_buffer
            wc_3d.struct_1.unk_2d0 = 0x0
            wc_3d.struct_1.unk_2d8 = 0x0
            wc_3d.struct_1.stencil_buffer_ptr2 = cmdbuf.stencil_buffer
            wc_3d.struct_1.stencil_buffer_ptr3 = cmdbuf.stencil_buffer
            wc_3d.struct_1.unk_2f0 = [0x0, 0x0, 0x0]
            wc_3d.struct_1.aux_fb_unk0 = 0x4
            wc_3d.struct_1.unk_30c = 0x0
            wc_3d.struct_1.aux_fb = AuxFBInfo(0xc000, 0, width, height)
            wc_3d.struct_1.unk_320_padding = bytes(0x10)
            wc_3d.struct_1.unk_partial_store_pipeline = Start3DStorePipelineBinding(
                cmdbuf.partial_store_pipeline_bind, cmdbuf.partial_store_pipeline | 4)#Start3DStorePipelineBinding(0xffff8212, 0xfffffff4)
            wc_3d.struct_1.partial_store_pipeline = Start3DStorePipelineBinding(
                cmdbuf.partial_store_pipeline_bind, cmdbuf.partial_store_pipeline | 4)
            wc_3d.struct_1.depth_clear_val2 = cmdbuf.depth_clear_value
            wc_3d.struct_1.stencil_clear_val2 = cmdbuf.stencil_clear_value
            wc_3d.struct_1.context_id = self.ctx_id
            wc_3d.struct_1.unk_376 = 0x0
            wc_3d.struct_1.unk_378 = 0x8
            wc_3d.struct_1.unk_37c = 0x0
            wc_3d.struct_1.unk_380 = 0x0
            wc_3d.struct_1.unk_388 = 0x0
            wc_3d.struct_1.depth_dimensions = (width - 1) | ((height - 1) << 15)

        if True:
            wc_3d.struct_2 = Start3DStruct2()
            wc_3d.struct_2.unk_0 = 0xa000
            wc_3d.struct_2.clear_pipeline = Start3DClearPipelineBinding(
                cmdbuf.load_pipeline_bind, cmdbuf.load_pipeline | 4)
            wc_3d.struct_2.unk_18 = 0x88
            wc_3d.struct_2.scissor_array = cmdbuf.scissor_array
            wc_3d.struct_2.depth_bias_array = cmdbuf.depth_bias_array
            wc_3d.struct_2.aux_fb =  wc_3d.struct_1.aux_fb
            wc_3d.struct_2.depth_dimensions = wc_3d.struct_1.depth_dimensions
            wc_3d.struct_2.unk_48 = 0x0
            wc_3d.struct_2.depth_flags = cmdbuf.ds_flags
            wc_3d.struct_2.depth_buffer_ptr1 = cmdbuf.depth_buffer
            wc_3d.struct_2.depth_buffer_ptr2 = cmdbuf.depth_buffer
            wc_3d.struct_2.stencil_buffer_ptr1 = cmdbuf.stencil_buffer
            wc_3d.struct_2.stencil_buffer_ptr2 = cmdbuf.stencil_buffer
            wc_3d.struct_2.unk_68 = [0] * 12
            wc_3d.struct_2.tvb_tilemap = tvb_tilemap._addr
            wc_3d.struct_2.tvb_heapmeta_addr = tvb_heapmeta._addr
            wc_3d.struct_2.unk_e8 = 0x50000000 * tile_blocks
            wc_3d.struct_2.tvb_heapmeta_addr2 = tvb_heapmeta._addr
            wc_3d.struct_2.unk_f8 = 0x10280 # TODO: varies 0, 0x280, 0x10000, 0x10280
            wc_3d.struct_2.aux_fb_ptr = self.aux_fb._addr
            wc_3d.struct_2.unk_108 = [0x0, 0x0, 0x0, 0x0, 0x0, 0x0]
            wc_3d.struct_2.pipeline_base = self.ctx.pipeline_base
            wc_3d.struct_2.unk_140 = 0x8c60
            wc_3d.struct_2.unk_148 = 0x0
            wc_3d.struct_2.unk_150 = 0x0
            wc_3d.struct_2.unk_158 = 0x1c
            wc_3d.struct_2.unk_160_padding = bytes(0x1e8)

        if True:
            wc_3d.struct_6 = Start3DStruct6()
            wc_3d.struct_6.unk_0 = 0x0
            wc_3d.struct_6.unk_8 = 0x0
            wc_3d.struct_6.unk_10 = 0x0
            wc_3d.struct_6.encoder_id = cmdbuf.encoder_id
            wc_3d.struct_6.unk_1c = 0xffffffff
            wc_3d.struct_6.unknown_buffer = self.unk_buf._addr
            wc_3d.struct_6.unk_28 = 0x0
            wc_3d.struct_6.unk_30 = 0x1
            wc_3d.struct_6.unk_34 = 0x1

        if True:
            wc_3d.struct_7 = Start3DStruct7()
            wc_3d.struct_7.unk_0 = 0x0
            wc_3d.struct_7.stamp1 = self.stamp_3d1
            wc_3d.struct_7.stamp2 = self.stamp_3d2
            wc_3d.struct_7.stamp_value = self.stamp_value
            wc_3d.struct_7.ev_3d = ev_3d.id
            wc_3d.struct_7.unk_20 = 0x0
            wc_3d.struct_7.unk_24 = 0x0 # check
            wc_3d.struct_7.uuid = uuid_3d
            wc_3d.struct_7.prev_stamp_value = 0x0
            wc_3d.struct_7.unk_30 = 0x0

        wc_3d.set_addr() # Update inner structure addresses
        #print("WC3D", hex(wc_3d._addr))
        #print(" s1", hex(wc_3d.struct_1._addr))
        #print(" s2", hex(wc_3d.struct_2._addr))
        #print(" s6", hex(wc_3d.struct_6._addr))
        #print(" s7", hex(wc_3d.struct_7._addr))

        ms = GPUMicroSequence(agx)

        start_3d = Start3DCmd()
        start_3d.struct1 = wc_3d.struct_1
        start_3d.struct2 = wc_3d.struct_2
        start_3d.buf_thing = buf_desc
        start_3d.unkptr_1c = agx.initdata.regionB.unkptr_178 + 8
        start_3d.unkptr_24 = wc_3d.unk_word._addr
        start_3d.struct6 = wc_3d.struct_6
        start_3d.struct7 = wc_3d.struct_7
        start_3d.cmdqueue_ptr = self.wq_3d.info._addr
        start_3d.workitem_ptr = wc_3d._addr
        start_3d.context_id = self.ctx_id
        start_3d.unk_50 = 0x1
        start_3d.unk_54 = 0x0
        start_3d.unk_58 = 0x2
        start_3d.unk_5c = 0x0
        start_3d.prev_stamp_value = 0x0
        start_3d.unk_68 = 0x0
        start_3d.unk_buf_ptr = wc_3d.unk_buf._addr
        start_3d.unk_buf2_ptr = wc_3d.unk_buf2._addr
        start_3d.unk_7c = 0x0
        start_3d.unk_80 = 0x0
        start_3d.unk_84 = 0x0
        start_3d.uuid = uuid_3d
        start_3d.attachments = []
        att_map = {
            ASAHI_ATTACHMENT_C: 0x2800,
            ASAHI_ATTACHMENT_Z: 0x4100,
            ASAHI_ATTACHMENT_S: 0x4100, # ???
        }

        fb = None
        depth = None

        for i in cmdbuf.attachments[:cmdbuf.attachment_count]:
            atype = att_map[i.type]
            start_3d.attachments.append(Attachment(i.pointer, att_map[i.type], 0x10017)) # FIXME check
            if fb is None and i.type == ASAHI_ATTACHMENT_C:
                fb = i.pointer
            if depth is None and i.type == ASAHI_ATTACHMENT_Z:
                depth = i.pointer
        start_3d.attachments += [Attachment(0, 0, 0)] * (16 - len(start_3d.attachments))
        start_3d.num_attachments = cmdbuf.attachment_count
        start_3d.unk_190 = 0x0

        ms.append(start_3d)

        ts1 = TimestampCmd()
        ts1.unk_1 = 0x0
        ts1.unk_2 = 0x0
        ts1.unk_3 = 0x80
        ts1.ts0_addr = wc_3d.ts1._addr
        ts1.ts1_addr = wc_3d.ts2._addr
        ts1.ts2_addr = wc_3d.ts2._addr
        ts1.cmdqueue_ptr = self.wq_3d.info._addr
        ts1.unk_24 = 0x0
        ts1.uuid = uuid_3d
        ts1.unk_30_padding = 0x0
        ms.append(ts1)

        ms.append(WaitForInterruptCmd(0, 1, 0))

        ts2 = TimestampCmd()
        ts2.unk_1 = 0x0
        ts2.unk_2 = 0x0
        ts2.unk_3 = 0x0
        ts2.ts0_addr = wc_3d.ts1._addr
        ts2.ts1_addr = wc_3d.ts2._addr
        ts2.ts2_addr = wc_3d.ts3._addr
        ts2.cmdqueue_ptr = self.wq_3d.info._addr
        ts2.unk_24 = 0x0
        ts2.uuid = uuid_3d
        ts2.unk_30_padding = 0x0
        ms.append(ts2)

        finish_3d = Finalize3DCmd()
        finish_3d.uuid = uuid_3d
        finish_3d.unk_8 = 0
        finish_3d.stamp = self.stamp_3d2
        finish_3d.stamp_value = self.stamp_value
        finish_3d.unk_18 = 0
        finish_3d.buf_thing = buf_desc
        finish_3d.buffer_mgr = self.buffer_mgr.info
        finish_3d.unk_2c = 1
        finish_3d.unkptr_34 = agx.initdata.regionB.unkptr_178 + 8
        finish_3d.struct7 = wc_3d.struct_7
        finish_3d.unkptr_44 = wc_3d.unk_word._addr
        finish_3d.cmdqueue_ptr = self.wq_3d.info._addr
        finish_3d.workitem_ptr = wc_3d._addr
        finish_3d.unk_5c = self.ctx_id
        finish_3d.unk_buf_ptr = wc_3d.unk_buf._addr
        finish_3d.unk_6c = 0
        finish_3d.unk_74 = 0
        finish_3d.unk_7c = 0
        finish_3d.unk_84 = 0
        finish_3d.unk_8c = 0
        finish_3d.startcmd_offset = -0x200
        finish_3d.unk_98 = 1
        ms.append(finish_3d)
        ms.finalize()

        wc_3d.microsequence_ptr = ms.obj._addr
        wc_3d.microsequence_size = ms.size

        #print(wc_3d)

        #ms.dump()
        #print(wc_3d)
        self.wq_3d.submit(wc_3d)

        ##### TA init

        #print(ctx_info)

        if not self.buffer_mgr_initialized:
            wc_initbm = agx.kobj.new(WorkCommandInitBM)
            wc_initbm.context_id = self.ctx_id
            wc_initbm.unk_8 = self.ctx_something
            wc_initbm.unk_c = 0
            wc_initbm.unk_10 = self.buffer_mgr.info.block_count
            wc_initbm.buffer_mgr = self.buffer_mgr.info
            wc_initbm.stamp_value = self.stamp_value

            self.wq_ta.submit(wc_initbm)

        ##### TA execution

        wc_ta = agx.kobj.new(WorkCommandTA)
        wc_ta.context_id = self.ctx_id
        wc_ta.unk_8 = 0
        wc_ta.event_control = self.event_control
        wc_ta.unk_14 = self.ctx_something
        wc_ta.buffer_mgr = self.buffer_mgr.info
        wc_ta.buf_thing = buf_desc
        wc_ta.unk_emptybuf_addr = wc_3d.unk_emptybuf_addr
        wc_ta.unk_34 = 0x0

        wc_ta.unk_154 = bytes(0x268)
        wc_ta.unk_3e8 = bytes(0x74)
        wc_ta.unk_594 = WorkCommand0_UnkBuf()

        wc_ta.ts1 = Timestamp()
        wc_ta.ts2 = Timestamp()
        wc_ta.ts3 = Timestamp()
        wc_ta.unk_5c4 = 0
        wc_ta.unk_5c8 = 0
        wc_ta.unk_5cc = 0
        wc_ta.unk_5d0 = 0
        wc_ta.unk_5d4 = 0x27 #1

        # Structures embedded in WorkCommandTA
        if True:
            wc_ta.tiling_params = tiling_params

        if True:
            wc_ta.struct_2 = StartTACmdStruct2()
            wc_ta.struct_2.unk_0 = 0x200
            wc_ta.struct_2.unk_8 = 0x1e3ce508 # fixed
            wc_ta.struct_2.unk_c = 0x1e3ce508 # fixed
            wc_ta.struct_2.tvb_tilemap = tvb_tilemap._addr
            wc_ta.struct_2.unkptr_18 = 0x0
            wc_ta.struct_2.unkptr_20 = tvb_something._addr
            wc_ta.struct_2.tvb_heapmeta_addr = tvb_heapmeta._addr | 0x8000000000000000
            wc_ta.struct_2.iogpu_unk_54 = 0x6b0003 # fixed
            wc_ta.struct_2.iogpu_unk_55 = 0x3a0012 # fixed
            wc_ta.struct_2.iogpu_unk_56 = 0x1 # fixed
            wc_ta.struct_2.unk_40 = 0x0 # fixed
            wc_ta.struct_2.unk_48 = 0xa000 # fixed
            wc_ta.struct_2.unk_50 = 0x88 # fixed
            wc_ta.struct_2.tvb_heapmeta_addr2 = tvb_heapmeta._addr
            wc_ta.struct_2.unk_60 = 0x0 # fixed
            wc_ta.struct_2.unk_68 = 0x0 # fixed
            wc_ta.struct_2.iogpu_deflake_1 = deflake_1
            wc_ta.struct_2.iogpu_deflake_2 = deflake_2
            wc_ta.struct_2.unk_80 = 0x1 # fixed
            wc_ta.struct_2.iogpu_deflake_3 = deflake_3
            wc_ta.struct_2.encoder_addr = cmdbuf.encoder_ptr
            wc_ta.struct_2.unk_98 = [0x0, 0x0] # fixed
            wc_ta.struct_2.unk_a8 = 0xa041 # fixed
            wc_ta.struct_2.unk_b0 = [0x0, 0x0, 0x0, 0x0, 0x0, 0x0] # fixed
            wc_ta.struct_2.pipeline_base = self.ctx.pipeline_base
            wc_ta.struct_2.unk_e8 = 0x0 # fixed
            wc_ta.struct_2.unk_f0 = 0x1c # fixed
            wc_ta.struct_2.unk_f8 = 0x8c60 # fixed
            wc_ta.struct_2.unk_100 = [0x0, 0x0, 0x0] # fixed
            wc_ta.struct_2.unk_118 = 0x1c # fixed

        if True:
            wc_ta.struct_3 = StartTACmdStruct3()
            wc_ta.struct_3.unk_480 = [0x0, 0x0, 0x0, 0x0, 0x0, 0x0] # fixed
            wc_ta.struct_3.unk_498 = 0x0 # fixed
            wc_ta.struct_3.unk_4a0 = 0x0 # fixed
            wc_ta.struct_3.iogpu_deflake_1 = deflake_1
            wc_ta.struct_3.unk_4ac = 0x0 # fixed
            wc_ta.struct_3.unk_4b0 = 0x0 # fixed
            wc_ta.struct_3.unk_4b8 = 0x0 # fixed
            wc_ta.struct_3.unk_4bc = 0x0 # fixed
            wc_ta.struct_3.unk_4c4_padding = bytes(0x48)
            wc_ta.struct_3.unk_50c = 0x0 # fixed
            wc_ta.struct_3.unk_510 = 0x0 # fixed
            wc_ta.struct_3.unk_518 = 0x0 # fixed
            wc_ta.struct_3.unk_520 = 0x0 # fixed
            wc_ta.struct_3.unk_528 = 0x0 # fixed
            wc_ta.struct_3.unk_52c = 0x0 # fixed
            wc_ta.struct_3.unk_530 = 0x0 # fixed
            wc_ta.struct_3.encoder_id = cmdbuf.encoder_id
            wc_ta.struct_3.unk_538 = 0x0 # fixed
            wc_ta.struct_3.unk_53c = 0xffffffff
            wc_ta.struct_3.unknown_buffer = wc_3d.struct_6.unknown_buffer
            wc_ta.struct_3.unk_548 = 0x0 # fixed
            wc_ta.struct_3.unk_550 = [
                0x0, 0x0, # fixed
                0x0, # 1 for boot stuff?
                0x0, 0x0, 0x0] # fixed
            wc_ta.struct_3.stamp1 = self.stamp_ta1
            wc_ta.struct_3.stamp2 = self.stamp_ta2
            wc_ta.struct_3.stamp_value = self.stamp_value
            wc_ta.struct_3.ev_ta = ev_ta.id
            wc_ta.struct_3.unk_580 = 0x0 # fixed
            wc_ta.struct_3.unk_584 = 0x0 # 1 for boot stuff?
            wc_ta.struct_3.uuid2 = uuid_ta
            #wc_ta.struct_3.unk_58c = [0x0, 0x0]
            wc_ta.struct_3.unk_58c = [0x1, 0x0]

        wc_ta.set_addr() # Update inner structure addresses
        #print("wc_ta", wc_ta)

        ms = GPUMicroSequence(agx)

        start_ta = StartTACmd()
        start_ta.tiling_params = wc_ta.tiling_params
        start_ta.struct2 = wc_ta.struct_2
        start_ta.buffer_mgr = self.buffer_mgr.info
        start_ta.buf_thing = buf_desc
        start_ta.unkptr_24 = agx.initdata.regionB.unkptr_170 + 4
        start_ta.cmdqueue_ptr = self.wq_ta.info._addr
        start_ta.context_id = self.ctx_id
        start_ta.unk_38 = 1
        start_ta.unk_3c = 1 #0
        start_ta.unk_40 = self.ctx_something
        start_ta.unk_48 = 1 #0
        start_ta.unk_50 = 0
        start_ta.struct3 = wc_ta.struct_3

        start_ta.unkptr_5c = wc_ta.unk_594._addr
        start_ta.unk_64 = 0x0 # fixed
        start_ta.uuid = uuid_ta
        start_ta.unk_70 = 0x0 # fixed
        start_ta.unk_74 = [ # fixed
            0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
            0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        ]
        start_ta.unk_15c = 0x0 # fixed
        start_ta.unk_160 = 0x0 # fixed
        start_ta.unk_168 = 0x0 # fixed
        start_ta.unk_16c = 0x0 # fixed
        start_ta.unk_170 = 0x0 # fixed
        start_ta.unk_178 = 0x0 # fixed
        ms.append(start_ta)

        ts1 = TimestampCmd()
        ts1.unk_1 = 0x0
        ts1.unk_2 = 0x0
        ts1.unk_3 = 0x80
        ts1.ts0_addr = wc_ta.ts1._addr
        ts1.ts1_addr = wc_ta.ts2._addr
        ts1.ts2_addr = wc_ta.ts2._addr
        ts1.cmdqueue_ptr = self.wq_ta.info._addr
        ts1.unk_24 = 0x0
        ts1.uuid = uuid_ta
        ts1.unk_30_padding = 0x0
        ms.append(ts1)

        ms.append(WaitForInterruptCmd(1, 0, 0))

        ts2 = TimestampCmd()
        ts2.unk_1 = 0x0
        ts2.unk_2 = 0x0
        ts2.unk_3 = 0x0
        ts2.ts0_addr = wc_ta.ts1._addr
        ts2.ts1_addr = wc_ta.ts2._addr
        ts2.ts2_addr = wc_ta.ts3._addr
        ts2.cmdqueue_ptr = self.wq_ta.info._addr
        ts2.unk_24 = 0x0
        ts2.uuid = uuid_ta
        ts2.unk_30_padding = 0x0
        ms.append(ts2)

        finish_ta = FinalizeTACmd()
        finish_ta.buf_thing = buf_desc
        finish_ta.buffer_mgr = self.buffer_mgr.info
        finish_ta.unkptr_14 = agx.initdata.regionB.unkptr_170 + 4
        finish_ta.cmdqueue_ptr = self.wq_ta.info._addr
        finish_ta.context_id = self.ctx_id
        finish_ta.unk_28 = 0x0 # fixed
        finish_ta.struct3 = wc_ta.struct_3
        finish_ta.unk_34 = 0x0 # fixed
        finish_ta.uuid = uuid_ta
        finish_ta.stamp = self.stamp_ta2
        finish_ta.stamp_value = self.stamp_value
        finish_ta.unk_48 = 0x0 # fixed
        finish_ta.unk_50 = 0x0 # fixed
        finish_ta.unk_54 = 0x0 # fixed
        finish_ta.unk_58 = 0x0 # fixed
        finish_ta.unk_60 = 0x0 # fixed
        finish_ta.unk_64 = 0x0 # fixed
        finish_ta.unk_68 = 0x0 # fixed
        finish_ta.startcmd_offset = -0x1e8 # fixed
        finish_ta.unk_70 = 0x0 # fixed
        ms.append(finish_ta)

        ms.finalize()

        wc_ta.unkptr_45c = tvb_something._addr
        wc_ta.tvb_size = tvb_something_size
        wc_ta.microsequence_ptr = ms.obj._addr
        wc_ta.microsequence_size = ms.size
        wc_ta.ev_3d = ev_3d.id
        wc_ta.stamp_value = self.stamp_value

        #ms.dump()

        #agx.mon.poll()

        #print(wc_ta)
        self.wq_ta.submit(wc_ta)

        ##### Run queues
        agx.ch.queue[0].q_3D.run(self.wq_3d, ev_3d.id)
        agx.ch.queue[0].q_TA.run(self.wq_ta, ev_ta.id)

        ##### Wait for work
        while not ev_3d.fired:
            agx.wait_for_events()

        if not ev_3d.fired:
            print("3D event didn't fire")

        #print("Stamps:")
        #print(self.stamp_ta1.pull())
        #print(self.stamp_ta2.pull())
        #print(self.stamp_3d1.pull())
        #print(self.stamp_3d2.pull())

        if fb is not None:
            print(f"Render {width}x{height}")
            base, obj = agx.find_object(fb)

            #unswizzle(agx, obj._paddr, width, height, 4, "fb.bin", grid=False)
            #os.system(f"convert -size {width}x{height} -depth 8 rgba:fb.bin frame{self.frames}.png")
            self.rframes += 1
            if not self.rframes & 1:
                agx.p.fb_blit(0, 0, width, height, obj._paddr, width, PIX_FMT.XBGR)

        if False and depth is not None:
            base, obj = agx.find_object(depth)

            width = align_up(width, 64)
            height = align_up(height, 64)

            unswizzle(agx, obj._paddr, width, height, 4, "depth.bin", grid=False)
            os.system(f"convert -size {width}x{height} -depth 8 rgba:depth.bin depth.png")

        #sys.exit(0)
