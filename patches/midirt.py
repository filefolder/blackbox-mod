"""DIN MIDI input: every realtime byte (F8..FF) passes through a message in progress, not only clock.

Stock parser (FUN_08044512, one per DIN port): a status byte resets the message being assembled, except 0xF8, which
is delivered on its own and leaves the message alone. MIDI allows any realtime byte (F8 clock, FA start, FB continue,
FC stop, FE active sensing, FF reset) between the bytes of another message; stock throws that message away for all
of them but clock. A keyboard that sends active sensing every 300 ms then eats a note-on or note-off now and then:
stuck or missing notes under a busy arp. Byte patches only, no code in the cave.
"""
PATCHES = [
    # cmp r5, #0xf8; beq realtime  ->  cmp r5, #0xf8; bhs realtime   (every byte >= 0xF8)
    (0x08044586, bytes.fromhex("d0d0"), bytes.fromhex("d0d2")),
    # realtime: movs r1, #0xf8 (always deliver clock)  ->  mov r1, r5 (deliver the byte that arrived)
    (0x08044530, bytes.fromhex("f821"), bytes.fromhex("2946")),
]
