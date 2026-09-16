#!/bin/bash


# Wipe stale output first (same mapid -> duplicate entries otherwise).
rm -rf tmp/split tmp/gmapsupp.img

mkdir -p tmp/split

# 1) Merge OSM + elevation  (skip if you just want a quick smoke test)
bin/osmosis-0.49.2/bin/osmosis --read-pbf file=data/united-kingdom-260914.osm.pbf \
    --read-pbf file=data/Hoehendaten_Freizeitkarte_GBR+.osm.pbf \
    --merge --write-pbf tmp/uk-ele.pbf omitmetadata=true

# 2) Split into tiles bounded by the polygon (parallelises mkgmap, saves RAM)
#    geonames -> place-name labels; sea -> coastlines. Both missing before = blank map.
java -Xmx48G -jar bin/splitter-r654/splitter.jar \
     --max-nodes=1200000 --mapid=21140001 \
     --polygon-file=polygons/great-britain.poly \
     --geonames-file=data/cities15000.zip \
     --precomp-sea=data/sea-latest.zip \
     --description="GB openfietsnew" \
     --output-dir=tmp/split tmp/uk-ele.pbf

# 3) mkgmap → gmapsupp.img   (fid=2114; uses the precompiled .typ binary, no gmt needed)
#    bounds -> addresses / administrative boundaries / derived POIs. Missing before = incomplete map.
java -Xmx62G -jar bin/mkgmap-r4924/mkgmap.jar \
     --read-config=styles/openfietsnew/template.args \
     --family-id=2114 --mapname=21140001 \
     --output-dir=tmp --gmapsupp --tdbfile --max-jobs=20 \
     --bounds=data/bounds-latest.zip \
     --precomp-sea=data/sea-latest.zip \
     --remove-ovm-work-files \
     --style-file=styles/openfietsnew \
     tmp/split/*.pbf styles/typ/openfietsnew.typ





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
