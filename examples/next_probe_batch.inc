; Generated manifest owns case order. Each phase stays below 16-bit frame wrap.
probe_batch_run:
    ld ix, probe_batch_plan
probe_batch_next:
    xor a
    ld (probe_batch_stop_after_case), a
    ld l, (ix+0)
    ld h, (ix+1)
    ld a, h
    or l
    ret z
    ld (probe_batch_id), hl
    ld a, (ix+2)
    ld (probe_batch_kind), a
    ld a, (ix+3)
    ld (probe_rate), a
    ld a, (ix+2)
    cp 5
    ld a, 1
    jr c, probe_batch_flow
    xor a
probe_batch_flow:
    ld (probe_flow), a
    ld d, (ix+4)
    ld a, 7
    call probe_nextreg_write
    ld a, (probe_batch_old_video)
    and 0xFB
    ld d, a
    ld a, (ix+5)
    cp 60
    jr nz, probe_batch_video
    set 2, d
probe_batch_video:
    ld a, 5
    call probe_nextreg_write
    ld a, (probe_flow)
    or a
    jr z, probe_batch_configure
    ld a, (probe_board)
    and 15
    cp 2
    jr z, probe_batch_configure
    cp 3
    scf
    ret nz                      ; never arm unsupported hardware
probe_batch_configure:
    call probe_set_rate
    ret c
    call probe_metrics_reset
    ld hl, (probe_batch_id)
    ld (probe_count), hl
    ld a, (probe_batch_kind)
    ld (probe_sum), a
    ld a, 7
    call probe_nextreg_read
    rrca
    rrca
    rrca
    rrca
    and 3
    ld (probe_sum+1), a
    ld a, 5
    call probe_nextreg_read
    and 4
    ld a, 50
    jr z, probe_batch_actual_hz
    ld a, 60
probe_batch_actual_hz:
    ld (probe_bad), a
    ld a, (ix+6)
    ld (probe_busy), a
    ld l, (ix+8)
    ld h, (ix+9)
    ld a, (probe_bad)
    cp 60
    jr nz, probe_batch_frames
    ; floor(frames*6/5) = frames + floor(frames/5), without multiply overflow.
    push hl
    ld de, 5
    ld bc, 0
probe_batch_divide5:
    or a
    sbc hl, de
    jr c, probe_batch_scale6
    inc bc
    jr probe_batch_divide5
probe_batch_scale6:
    pop hl
    add hl, bc
probe_batch_frames:
    ld (probe_batch_target), hl
    ld (probe_duration), hl
    ld a, 12
    call probe_emit
    ret c
    ld a, (probe_sum+1)
    cp (ix+4)
    jp nz, probe_batch_config_failed
    ld a, (probe_bad)
    cp (ix+5)
    jp nz, probe_batch_config_failed
probe_batch_dispatch:
    ld a, (probe_batch_kind)
    or a
    jr nz, probe_batch_fault
    call probe_soak_phase
    jr probe_batch_finished
probe_batch_fault:
    cp 1
    jr z, probe_batch_receive
    cp 5
    jr z, probe_batch_receive
    cp 4
    jr z, probe_batch_corrupt
    call probe_batch_fault_test
    jr probe_batch_finished
probe_batch_receive:
    call probe_rx_test
    jr c, probe_batch_finished
    ld a, (probe_result)
    or a
    jr z, probe_batch_finished
    scf
    jr probe_batch_finished
probe_batch_corrupt:
    call probe_soak_chunk
    jr nc, probe_batch_failed ; intentional corruption must actually be seen
    ld a, (probe_result)
    cp 2
    jr nz, probe_batch_failed
    ld hl, (probe_bad)
    dec hl
    ld a, h
    or l
    jr nz, probe_batch_failed
    ld a, (probe_errors)
    or a
    jr nz, probe_batch_failed
    ld hl, (probe_count)
    ld de, 8192
    or a
    sbc hl, de
    jr probe_batch_finished
probe_batch_config_failed:
    ld a, 1
    ld (probe_batch_stop_after_case), a
probe_batch_failed:
    scf
probe_batch_finished:
    ld a, 0
    adc a, 0
    ld (probe_batch_failed_case), a
    call probe_metrics_reset
    ld a, (probe_batch_failed_case)
    ld (probe_result), a
    ld hl, (probe_batch_id)
    ld (probe_count), hl
    ld a, 13
    call probe_emit
    ret c
    ld a, (probe_batch_failed_case)
    or a
    jr z, probe_batch_advance
    ld a, (probe_batch_stop_after_case)
    or a
    jr nz, probe_batch_stop
    ld a, (probe_batch_kind)
    or a
    jr z, probe_batch_check_payload
    cp 4
    jr nz, probe_batch_recover
probe_batch_check_payload:
    ld a, (probe_batch_blocks)
    or a
    jr nz, probe_batch_stop ; incomplete promised TX bytes cannot delimit further reports
probe_batch_recover:
    call probe_recover
    ret c
probe_batch_advance:
    ld de, 16
    add ix, de
    jp probe_batch_next
probe_batch_stop:
    scf
    ret

probe_batch_pause:
    ld l, (ix+14)
    ld h, (ix+15)
    ld a, h
    or l
    ret z
probe_batch_pause_loop:
    halt
    dec hl
    ld a, h
    or l
    jr nz, probe_batch_pause_loop
    ret

probe_batch_rx_report:
    ld a, (probe_batch_kind)
    cp 5
    jr z, probe_batch_overflow
    ld de, 32768
    call uart_flush_budget
    ld a, 2
    jp probe_emit
probe_batch_overflow:
    call probe_batch_guard_flush
    ld a, 2
    call probe_emit
    ret c
    jp probe_batch_fault_report

; Snapshot before guarded read/flush. The snapshot survives deliberate recovery.
probe_batch_guard_flush:
    ld a, (uart_rx_errors)
    ld (probe_batch_fault_errors), a
    ld hl, (probe_count)
    ld (probe_batch_fault_count), hl
    xor a
    ld (probe_batch_guard), a
    ld (probe_batch_flushed), a
    call uart_read
    jr c, probe_batch_flush
    ld a, 1
    ld (probe_batch_guard), a
probe_batch_flush:
    ld a, (probe_batch_kind)
    cp 6
    jr nz, probe_batch_flush_matching
    ld hl, uart_baud_115200
    call uart_set_baud           ; no AT text while FPGA/ESP rates differ
probe_batch_flush_matching:
    ld de, 32768
    call uart_flush_budget
    ret c
    ld a, (uart_rx_errors)
    or a
    ret nz
    inc a
    ld (probe_batch_flushed), a
    ret
probe_batch_fault_report:
    call probe_metrics_reset
    ld hl, (probe_batch_id)
    ld (probe_count), hl
    ld hl, (probe_batch_fault_count)
    ld (probe_sum), hl
    ld a, (probe_batch_guard)
    ld (probe_bad), a
    ld a, (probe_batch_flushed)
    ld (probe_busy), a
    ld a, (probe_batch_fault_errors)
    ld (probe_errors), a
    ld b, 4
    ld a, (probe_batch_kind)
    cp 6
    jr nz, probe_batch_fault_mask
    ld b, 0x40
probe_batch_fault_mask:
    ld a, (probe_errors)
    and b
    jr z, probe_batch_fault_fail
    ld a, (probe_batch_guard)
    and 1
    jr z, probe_batch_fault_fail
    ld a, (probe_batch_flushed)
    and 1
    jr nz, probe_batch_fault_emit
probe_batch_fault_fail:
    ld a, 1
    ld (probe_result), a
probe_batch_fault_emit:
    ld a, 15
    call probe_emit
    ret c
    ld a, (probe_result)
    or a
    ret z
    scf
    ret

probe_batch_fault_test:
    call probe_metrics_reset
    xor a
    ld (probe_connected_seen), a
    ld (probe_batch_closed_seen), a
    ld (probe_batch_fault_count), a
    ld (probe_batch_fault_count+1), a
    call probe_soak_reset
    ld a, (probe_batch_kind)
    cp 6
    call z, probe_step_next
    ld hl, (probe_batch_id)
    ld (probe_count), hl
    ld a, (probe_batch_kind)
    ld (probe_reserved), a
    ld hl, (probe_ticks)
    ld (probe_batch_fault_started), hl
    call probe_deadline
    ld a, 14
    call probe_soak_emit
    ret c
    ld a, (probe_batch_kind)
    cp 3
    jp z, probe_batch_reconnect
    call probe_deadline
    ld a, (probe_batch_kind)
    cp 6
    jp z, probe_batch_framing
    ld hl, (probe_ticks)
    ld (probe_batch_fault_started), hl
probe_batch_silence:
    call uart_read_raw
    jr c, probe_batch_silence_failed
    ld a, (uart_rx_errors)
    or a
    jr nz, probe_batch_silence_failed
    call probe_batch_fault_elapsed
    ld de, 25
    or a
    sbc hl, de
    jr c, probe_batch_silence
    ld hl, 1
    ld (probe_sum), hl
    call probe_batch_fault_elapsed
    ld (probe_duration), hl
    jp probe_batch_fault_emit
probe_batch_silence_failed:
    ld a, (uart_rx_errors)
    ld (probe_errors), a
    ld a, 1
    ld (probe_result), a
    jp probe_batch_fault_emit
probe_batch_reconnect:
    call probe_expired
    jr nc, probe_batch_challenge_failed
    call probe_soak_service
    ld a, (uart_rx_errors)
    or a
    jr nz, probe_batch_challenge_failed
    ld a, (probe_soak_protocol_error)
    or a
    jr nz, probe_batch_challenge_failed
    ld hl, (probe_soak_rx_count)
    ld de, 32
    or a
    sbc hl, de
    jr c, probe_batch_reconnect
    jr nz, probe_batch_challenge_failed
    ld a, (probe_soak_parse_state)
    or a
    jr nz, probe_batch_reconnect
    ld hl, (probe_soak_rx_bad)
    ld a, h
    or l
    jr nz, probe_batch_challenge_failed
    ld hl, (probe_soak_rx_sum)
    ld de, 3341                 ; sum of 32 bytes from the seed-1/B8 LFSR
    or a
    sbc hl, de
    jr z, probe_batch_challenge_report
probe_batch_challenge_failed:
    ld a, 1
    ld (probe_result), a
probe_batch_challenge_report:
    ld hl, (probe_soak_rx_count)
    ld (probe_busy), hl
    ld hl, (probe_soak_rx_sum)
    ld (probe_sum), hl
    ld hl, (probe_soak_rx_bad)
    ld (probe_bad), hl
    ld a, (uart_rx_errors)
    ld (probe_errors), a
    call probe_batch_fault_elapsed
    ld (probe_duration), hl
    ld a, (probe_batch_closed_seen)
    ld b, a
    ld a, (probe_connected_seen)
    add a, a
    or b
    ld (probe_reserved+1), a      ; independent notices are diagnostic only
    jp probe_batch_fault_emit
probe_batch_framing:
    ld hl, uart_baud_9600
    call uart_set_baud
    ld hl, (probe_ticks)
    ld (probe_batch_fault_started), hl
probe_batch_framing_poll:
    call uart_read_raw
    jr nc, probe_batch_framing_time
    ld hl, (probe_batch_fault_count)
    inc hl
    ld (probe_batch_fault_count), hl
probe_batch_framing_time:
    call probe_batch_fault_elapsed
    ld e, (ix+14)
    ld d, (ix+15)
    or a
    sbc hl, de
    jr c, probe_batch_framing_poll
    ld hl, (probe_batch_fault_count)
    ld (probe_count), hl
    call probe_batch_guard_flush
    jp probe_batch_fault_report
probe_batch_fault_elapsed:
    ld hl, (probe_ticks)
    ld de, (probe_batch_fault_started)
    or a
    sbc hl, de
    ret

probe_batch_old_video: db 0
probe_batch_id: dw 0
probe_batch_kind: db 0
probe_batch_target: dw 0
probe_batch_blocks: db 0
probe_batch_failed_case: db 0
probe_batch_stop_after_case: db 0
probe_batch_closed_seen: db 0
probe_batch_closed: db "0,CLOSED",0
probe_batch_fault_errors: db 0
probe_batch_fault_count: dw 0
probe_batch_guard: db 0
probe_batch_flushed: db 0
probe_batch_fault_started: dw 0
    include "batch_plan.inc"
