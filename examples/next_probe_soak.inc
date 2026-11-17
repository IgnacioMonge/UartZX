; Soak keeps active +IPD parsing alive throughout commands, prompts and TX.
    IFNDEF PROBE_SOAK_FRAMES
PROBE_SOAK_FRAMES EQU 15000
    ENDIF
    ASSERT PROBE_SOAK_FRAMES > 0 && PROBE_SOAK_FRAMES*6/5 < 65536

probe_soak_phase:
    IFDEF PROBE_BATTERY
    ld hl, (probe_batch_target)
    ELSE
    ld a, 5
    call probe_nextreg_read
    and 4
    ld hl, PROBE_SOAK_FRAMES
    jr z, probe_soak_target
    ld hl, PROBE_SOAK_FRAMES*6/5
    ENDIF
probe_soak_target:
    ld (probe_soak_target_frames), hl
    ld hl, (probe_ticks)         ; one Z80 instruction: IRQ cannot tear the word
    ld (probe_soak_phase_started), hl
    ld hl, 0
    ld (probe_soak_chunks), hl
probe_soak_next:
    call probe_soak_chunk
    push af
    ld hl, (probe_soak_chunks)
    inc hl
    ld (probe_soak_chunks), hl
    pop af
    jr c, probe_soak_phase_end
    call probe_soak_elapsed
    ld de, (probe_soak_target_frames)
    or a
    sbc hl, de
    jr c, probe_soak_next
probe_soak_phase_end:
    push af
    call probe_metrics_reset
    pop af
    ld a, 1
    jr c, probe_soak_phase_result
    xor a
probe_soak_phase_result:
    ld (probe_result), a
    call probe_soak_elapsed
    ld (probe_duration), hl
    ld hl, (probe_soak_chunks)
    ld (probe_count), hl
    ld hl, (probe_soak_target_frames)
    ld (probe_reserved), hl
    call probe_deadline
    ld a, 11
    call probe_soak_emit
    ret c
    ld a, (probe_result)
    or a
    ret z
    IFDEF PROBE_BATTERY
    scf
    ret
    ENDIF
    ; Bounded best effort cleanup after a failed chunk; preserve failure.
    call probe_recover
    ld hl, probe_close
    call probe_command
    ld hl, probe_server_off
    call probe_command
    scf
    ret
probe_soak_elapsed:
    ld hl, (probe_ticks)
    ld de, (probe_soak_phase_started)
    or a
    sbc hl, de
    ret

probe_soak_chunk:
    call probe_metrics_reset
    call probe_step_next
    call probe_soak_reset
    ld hl, 8192
    ld (probe_count), hl
    IFDEF PROBE_BATTERY
    ld a, 4
    ld (probe_batch_blocks), a
    ld hl, 8192
    ELSE
    ld hl, 2048
    ENDIF
    ld (probe_reserved), hl
    ld hl, (probe_ticks)
    ld (probe_soak_chunk_started), hl
    call probe_deadline
    ld a, 9
    call probe_soak_emit
    jp c, probe_soak_timeout
probe_soak_first_rx:
    ld hl, (probe_soak_rx_count)
    ld a, h
    or l
    jr nz, probe_soak_tx_start
    call probe_soak_service
    call probe_expired
    jr c, probe_soak_first_rx
    jr probe_soak_timeout
probe_soak_tx_start:
    ld hl, probe_soak_send_payload
    call probe_soak_send_command
    jr c, probe_soak_timeout
    ld bc, 2048
probe_soak_tx_loop:
    ld a, (probe_soak_tx_pattern)
    call probe_soak_put
    jr c, probe_soak_timeout
    ld a, 1
    ld (probe_soak_tx_active), a
    ld a, (probe_soak_tx_pattern)
    srl a
    jr nc, probe_soak_tx_pattern_store
    xor 0xB8
probe_soak_tx_pattern_store:
    ld (probe_soak_tx_pattern), a
    dec bc
    ld a, b
    or c
    jr nz, probe_soak_tx_loop
    IFDEF PROBE_BATTERY
    ld a, (probe_batch_blocks)
    cp 1
    jr nz, probe_soak_payload_sent
    ENDIF
    xor a
    ld (probe_soak_tx_active), a
probe_soak_payload_sent:
    call probe_soak_wait_send
    jr c, probe_soak_timeout
    IFDEF PROBE_BATTERY
    ld hl, probe_batch_blocks
    dec (hl)
    jr nz, probe_soak_tx_start
    xor a
    ld (probe_soak_tx_active), a
    ENDIF
probe_soak_rx_wait:
    call probe_soak_service
    ld hl, (probe_soak_rx_count)
    ld de, 8192
    or a
    sbc hl, de
    jr z, probe_soak_chunk_done
    call probe_expired
    jr c, probe_soak_rx_wait
probe_soak_timeout:
    ld a, 1
    ld (probe_result), a
probe_soak_chunk_done:
    xor a
    ld (probe_soak_tx_active), a
    ld hl, (probe_soak_rx_count)
    ld (probe_count), hl
    ld hl, (probe_soak_rx_sum)
    ld (probe_sum), hl
    ld hl, (probe_soak_rx_bad)
    ld (probe_bad), hl
    ld hl, (probe_soak_overlap)
    ld (probe_reserved), hl
    ld hl, (probe_ticks)
    ld de, (probe_soak_chunk_started)
    or a
    sbc hl, de
    ld (probe_duration), hl
    ld a, (uart_rx_errors)
    ld (probe_errors), a
    ld a, (probe_result)
    or a
    jr nz, probe_soak_chunk_report
    ld hl, (probe_bad)
    ld a, h
    or l
    ld a, 2
    jr nz, probe_soak_chunk_set_result
    ld a, (probe_soak_protocol_error)
    or a
    ld a, 2
    jr nz, probe_soak_chunk_set_result
    ld a, (probe_errors)
    or a
    jr z, probe_soak_chunk_report
    ld a, 3
probe_soak_chunk_set_result:
    ld (probe_result), a
probe_soak_chunk_report:
    call probe_deadline
    ld a, 10
    call probe_soak_emit
    ret c
    ld a, (probe_result)
    or a
    ret z
    scf
    ret

probe_soak_reset:
    xor a
    ld hl, probe_soak_state
    ld de, probe_soak_state+1
    ld bc, probe_soak_state_end-probe_soak_state-1
    ld (hl), a
    ldir
    inc a
    ld (probe_soak_rx_pattern), a
    ld (probe_soak_tx_pattern), a
    ret

probe_soak_emit:
    ld (probe_event), a
    ld bc, UART_FRAME
    in a, (c)
    ld (probe_frame), a
    ld hl, probe_send_report
    call probe_soak_send_command
    ret c
    ld hl, probe_report
    ld b, 32
probe_soak_emit_bytes:
    ld a, (hl)
    call probe_soak_put
    ret c
    inc hl
    djnz probe_soak_emit_bytes
    jp probe_soak_wait_send
probe_soak_send_command:
    xor a
    ld (probe_soak_send_seen), a
    ld (probe_soak_prompt_seen), a
probe_soak_command_bytes:
    ld a, (hl)
    or a
    jr z, probe_soak_prompt_wait
    call probe_soak_put
    ret c
    inc hl
    jr probe_soak_command_bytes
probe_soak_prompt_wait:
    call probe_soak_service
    ld a, (probe_soak_prompt_seen)
    or a
    ret nz
    call probe_expired
    jr c, probe_soak_prompt_wait
    scf
    ret
probe_soak_wait_send:
    call probe_soak_service
    ld a, (probe_soak_send_seen)
    or a
    ret nz
    ld a, (probe_soak_protocol_error)
    or a
    jr nz, probe_soak_wait_failed
    call probe_expired
    jr c, probe_soak_wait_send
probe_soak_wait_failed:
    scf
    ret
probe_soak_put:
    push de
    ld e, a
probe_soak_put_retry:
    call probe_soak_service       ; also service successful writes, not only busy
    call probe_expired
    jr nc, probe_soak_put_failed
    ld a, e
    call uart_try_write
    jr nc, probe_soak_put_done
    push hl
    ld hl, (probe_busy)
    inc hl
    ld (probe_busy), hl
    pop hl
    jr probe_soak_put_retry
probe_soak_put_failed:
    scf
probe_soak_put_done:
    pop de
    ret

; One nonblocking RX sample per call. All caller registers except AF survive.
probe_soak_service:
    push bc
    push de
    push hl
    call uart_read_raw
    call c, probe_soak_parse
    pop hl
    pop de
    pop bc
    ret
probe_soak_parse:
    ld e, a
    ld a, (probe_soak_parse_state)
    cp 3
    jp z, probe_soak_body
    cp 2
    jr z, probe_soak_length
    cp 1
    jr z, probe_soak_prefix
    ld a, e
    cp '+'
    jr z, probe_soak_prefix_start
    cp '>'
    jr z, probe_soak_prompt_found
    cp 13
    ret z
    cp 10
    jp z, probe_soak_line_end
    ld a, (probe_soak_line_size)
    cp 94
    jr nc, probe_soak_parse_error
    ld c, a
    ld b, 0
    ld hl, probe_line_buffer
    add hl, bc
    ld (hl), e
    inc a
    ld (probe_soak_line_size), a
    ret
probe_soak_prompt_found:
    ld a, 1
    ld (probe_soak_prompt_seen), a
    xor a
    ld (probe_soak_line_size), a
    ret
probe_soak_prefix_start:
    ld hl, probe_ipd_prefix
    ld (probe_soak_prefix_pointer), hl
    ld a, 1
    ld (probe_soak_parse_state), a
    ret
probe_soak_prefix:
    ld hl, (probe_soak_prefix_pointer)
    ld a, e
    cp (hl)
    jr nz, probe_soak_parser_reset
    inc hl
    ld (probe_soak_prefix_pointer), hl
    ld a, (hl)
    or a
    ret nz
    ld hl, 0
    ld (probe_soak_packet_left), hl
    xor a
    ld (probe_soak_digits), a
    ld a, 2
    ld (probe_soak_parse_state), a
    ret
probe_soak_length:
    ld a, e
    cp ':'
    jr z, probe_soak_length_end
    sub '0'
    cp 10
    jr nc, probe_soak_parse_error
    ld e, a
    ld d, 0
    ld a, (probe_soak_digits)
    inc a
    cp 5
    jr nc, probe_soak_parse_error
    ld (probe_soak_digits), a
    ld hl, (probe_soak_packet_left)
    ld b, h
    ld c, l
    add hl, hl
    add hl, hl
    add hl, bc
    add hl, hl
    add hl, de
    ld (probe_soak_packet_left), hl
    ret
probe_soak_length_end:
    ld hl, (probe_soak_packet_left)
    ld a, h
    or l
    jr z, probe_soak_parse_error
    ld de, 8193
    or a
    sbc hl, de
    jr nc, probe_soak_parse_error
    ld a, 3
    ld (probe_soak_parse_state), a
    ret
probe_soak_parse_error:
    ld a, 1
    ld (probe_soak_protocol_error), a
probe_soak_parser_reset:
    xor a
    ld (probe_soak_parse_state), a
    ld (probe_soak_line_size), a
    ret
probe_soak_body:
    ld a, (probe_soak_rx_pattern)
    cp e
    jr z, probe_soak_body_match
    ld hl, (probe_soak_rx_bad)
    inc hl
    ld (probe_soak_rx_bad), hl
probe_soak_body_match:
    ld d, 0
    ld hl, (probe_soak_rx_sum)
    add hl, de
    ld (probe_soak_rx_sum), hl
    ld hl, (probe_soak_rx_count)
    inc hl
    ld (probe_soak_rx_count), hl
    ld a, (probe_soak_tx_active)
    or a
    jr z, probe_soak_body_pattern
    ld hl, (probe_soak_overlap)
    inc hl
    ld (probe_soak_overlap), hl
probe_soak_body_pattern:
    ld a, (probe_soak_rx_pattern)
    srl a
    jr nc, probe_soak_rx_pattern_store
    xor 0xB8
probe_soak_rx_pattern_store:
    ld (probe_soak_rx_pattern), a
    ld hl, (probe_soak_packet_left)
    dec hl
    ld (probe_soak_packet_left), hl
    ld a, h
    or l
    ret nz
    jp probe_soak_parser_reset
probe_soak_line_end:
    ld a, (probe_soak_line_size)
    ld c, a
    ld b, 0
    ld hl, probe_line_buffer
    add hl, bc
    ld (hl), 0
    xor a
    ld (probe_soak_line_size), a
    ld hl, probe_line_buffer
    ld de, probe_send_ok
    call probe_equal
    jr nz, probe_soak_line_error
    ld a, 1
    ld (probe_soak_send_seen), a
    ret
probe_soak_line_error:
    IFDEF PROBE_BATTERY
    ld hl, probe_line_buffer
    ld de, probe_batch_closed
    call probe_equal
    jr nz, probe_batch_line_connect
    ld a, 1
    ld (probe_batch_closed_seen), a
    ret
probe_batch_line_connect:
    ld hl, probe_line_buffer
    ld de, probe_connected
    call probe_equal
    jr nz, probe_batch_line_error
    ld a, 1
    ld (probe_connected_seen), a
    ret
probe_batch_line_error:
    ENDIF
    ld hl, probe_line_buffer
    ld de, probe_error
    call probe_equal
    ret nz
    jp probe_soak_parse_error

probe_soak_send_payload: db "AT+CIPSEND=0,2048",13,10,0
probe_soak_phase_started: dw 0
probe_soak_chunk_started: dw 0
probe_soak_target_frames: dw 0
probe_soak_chunks: dw 0
probe_soak_state:
probe_soak_parse_state: db 0
probe_soak_prefix_pointer: dw 0
probe_soak_packet_left: dw 0
probe_soak_digits: db 0
probe_soak_line_size: db 0
probe_soak_prompt_seen: db 0
probe_soak_send_seen: db 0
probe_soak_protocol_error: db 0
probe_soak_tx_active: db 0
probe_soak_rx_pattern: db 1
probe_soak_tx_pattern: db 1
probe_soak_rx_count: dw 0
probe_soak_rx_sum: dw 0
probe_soak_rx_bad: dw 0
probe_soak_overlap: dw 0
probe_soak_state_end:
