; Optional effective rates for the two fast baud tables, rounded to kHz.
; Each 16-bit row is indexed by (NextReg 0x11 & 7), like next_baud_*.inc.
; Format this integer as decimal and append "000" in the application's
; AT+UART_CUR command. This avoids runtime division/32-bit formatting in
; the driver. Rounding differs from the divisor's rate by at most 500 baud
; (under 0.05% here); it is not a measurement of the oscillator or the ESP.
; No code or state; both rows together cost 32 bytes. Divisor tables remain
; optional and must match these rows; tests check all 16 entries.

uart_baud_khz_1152000:
    defw 1167, 1143, 1133, 1154, 1148, 1143, 1138, 1174
uart_baud_khz_2000000:
    defw 2000, 2041, 1964, 2000, 1938, 2000, 1941, 1929
