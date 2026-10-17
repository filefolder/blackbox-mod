#!/bin/sh
# Compile src/*.c, src/*.S and src/munchi.cpp into the code cave: out/cave.elf + out/cave.bin (spliced into the image
# by patch.py). One image, one file: the cave is everything, appended after the stock image in flash bank 1.
# (Upstream's second flash bank and its card loader are not in this build.)
# MUNCHI=0 ./build.sh leaves Munchi Delay's engine out (its Type slot then reads "---").
set -e
cd "$(dirname "$0")"
mkdir -p out out/obj

CFLAGS="-mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 -Os -ffreestanding -nostdlib -fno-builtin -Wall -Wextra -Werror"

[ "${MUNCHI:-1}" = 1 ] || MUNCHI_FLAG=-DMUNCHI_OFF
arm-none-eabi-g++ $MUNCHI_FLAG -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 -Os -ffreestanding -nostdlib -fno-exceptions \
  -fno-rtti -fno-threadsafe-statics -fno-use-cxa-atexit -fno-math-errno -std=gnu++17 -Wall -Werror -Wno-unused-function \
  -Wno-class-memaccess -Wno-unused-but-set-variable -Wno-unused-variable -Wno-unused-parameter -isystem src/munchi/sys -c -o out/munchi.o src/munchi.cpp

OBJS=""
for f in src/*.c; do
  b=$(basename "$f" .c)
  arm-none-eabi-gcc $CFLAGS -c -o "out/obj/$b.o" "$f"
  OBJS="$OBJS out/obj/$b.o"
done
for f in src/*.S; do
  b=$(basename "$f" .S)
  arm-none-eabi-gcc -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 -c -o "out/obj/$b.S.o" "$f"
  OBJS="$OBJS out/obj/$b.S.o"
done

# src/cave.ld: one region, 0x080F1E78 up to the end of bank 1 (0x08100000), which the installer will not go past
arm-none-eabi-gcc -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 -nostdlib \
  -T src/cave.ld -Wl,-e,0 -Wl,-Map=out/cave.map -o out/cave.elf $OBJS out/munchi.o
arm-none-eabi-objcopy -O binary -j .cave out/cave.elf out/cave.bin
arm-none-eabi-size -A out/cave.elf | grep -E 'cave|data|bss'
