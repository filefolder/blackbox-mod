/* Code cave: appended directly after the stock image (which ends at 0x080F1E78). The image starts at 0x08040000 and
   the installer refuses one that runs past the end of flash bank 1, 0x08100000 (3.1.G was 76 bytes over: "unable to
   verify", as reported). So the cave is 0xE188 = 57,736 bytes at most, and the linker now enforces it. */
MEMORY { CAVE (rx) : ORIGIN = 0x080F1E78, LENGTH = 0xE188 }
SECTIONS {
  .cave : { *(.text .text.* .rodata .rodata.*) } > CAVE
  /DISCARD/ : { *(.data .data.* .bss .bss.* COMMON .ARM.exidx* .comment) }
}
