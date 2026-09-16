#!/bin/bash
# Create and run a QEMU Windows VM with a dynamically growing qcow2 disk.
# All paths are derived from this script's own location (./VMs/), so it works
# regardless of $HOME or the current working directory. The disk grows on demand
# automatically; only what the guest writes is used, capped at DISK_SIZE. To
# raise the cap later: qemu-img resize "$DISK" +50G
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VM_NAME="${1:-win}"
DISK_DIR="$SCRIPT_DIR/$VM_NAME"
DISK="$DISK_DIR/win.qcow2"
DISK_SIZE="${DISK_SIZE:-60G}"          # cap; usage starts near 0 and grows on demand

WINDOWS_ISO="${WINISO:-$SCRIPT_DIR/Win10_22H2_EnglishInternational_x64v1.iso}"   # Windows installer ISO (override with WINISO=...)

mkdir -p "$DISK_DIR"

if [ ! -f "$WINDOWS_ISO" ]; then
    echo "ERROR: Windows ISO not found at $WINDOWS_ISO (set WINISO=... to point elsewhere)"; exit 1
fi

# Create the grow-on-demand disk only if it does not already exist.
if [ ! -f "$DISK" ]; then
    qemu-img create -f qcow2 "$DISK" "$DISK_SIZE"
fi

# dd if=/dev/zero of=garmin-sd.img bs=1M count=2048
# 
# sudo parted garmin-sd.img --script mklabel msdos
# sudo parted garmin-sd.img --script mkpart primary fat32 128s 4188287s
# 
# sudo sfdisk --part-type garmin-sd.img 1 c
# 
# LOOP=$(sudo losetup --find --show --partscan "garmin-sd.img")
# echo "Loop device: $LOOP"
# 
# PART="${LOOP}p1"
# 
# if [[ ! -b "$PART" ]]; then
#     echo "ERROR: Could not find partition $PART"
#     sudo fdisk -l "garmin-sd.img"
#     sudo losetup -d "$LOOP"
#     exit 1
# fi
# 
# sudo mkfs.fat -F 32 -n GARMIN "$PART"
# 
# mkdir -p /tmp/garmin-sd
# sudo mount "$PART" /tmp/garmin-sd
# 
# sudo mkdir -p /tmp/garmin-sd/Garmin
# sudo cp ../tmp/gmapsupp.img /tmp/garmin-sd/Garmin/
# 
# sync
# 
# ls -lh /tmp/garmin-sd/Garmin/gmapsupp.img
# 
# sudo umount /tmp/garmin-sd
# sudo losetup -d "$LOOP"

exec qemu-system-x86_64 \
    -enable-kvm \
    -m 16G -smp 16 \
    -display gtk,gl=on \
    -object memory-backend-memfd,id=mem1,size=16G \
    -machine memory-backend=mem1 \
    -device virtio-vga-gl,blob=on,hostmem=16G,drm_native_context=on \
    -device virtio-tablet-pci \
    -device virtio-keyboard-pci \
    -drive file="$DISK",format=qcow2,if=ide,index=0,media=disk \
    -cdrom "./virtio-win-0.1.302.iso"  \
    -vga none \
    -virtfs local,path=./shared-dir,mount_tag=host0,security_model=passthrough,id=host0 \
    -netdev user,id=net0 -device e1000,netdev=net0 \
    -drive file="$SCRIPT_DIR/garmin-sd.img",format=raw,if=none,id=garmin \
    -device qemu-xhci,id=xhci \
    -device usb-storage,drive=garmin,removable=on

    # -cdrom "$WINDOWS_ISO"  \
    # -device virtio-net-pci,netdev=n0 \
    # -netdev user,id=n0 \
