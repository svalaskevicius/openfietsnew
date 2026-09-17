#!/bin/bash

cd VMs || exit 1

dd if=/dev/zero of=garmin-sd.img bs=1M count=2048

sudo parted garmin-sd.img --script mklabel msdos
sudo parted garmin-sd.img --script mkpart primary fat32 128s 4188287s

sudo sfdisk --part-type garmin-sd.img 1 c

LOOP=$(sudo losetup --find --show --partscan "garmin-sd.img")
echo "Loop device: $LOOP"

PART="${LOOP}p1"

if [[ ! -b "$PART" ]]; then
    echo "ERROR: Could not find partition $PART"
    sudo fdisk -l "garmin-sd.img"
    sudo losetup -d "$LOOP"
    exit 1
fi

sudo mkfs.fat -F 32 -n GARMIN "$PART"

mkdir -p /tmp/garmin-sd
sudo mount "$PART" /tmp/garmin-sd

sudo mkdir -p /tmp/garmin-sd/Garmin
sudo cp ../tmp/gmapsupp.img /tmp/garmin-sd/Garmin/

sync

sudo umount /tmp/garmin-sd
sudo losetup -d "$LOOP"
