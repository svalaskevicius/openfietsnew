#!/bin/bash


export PATH="$HOME/src/openfietsnew/bin/mkgmap-r4924:$HOME/src/openfietsnew/bin/splitter-r654:$HOME/src/openfietsnew/bin/osmosis-0.49.2/bin/:$PATH"

mkdir -p tmp/split

# 1) Merge OSM + elevation  (skip if you just want a quick smoke test)
osmosis --read-pbf file=data/united-kingdom-260914.osm.pbf \
    --read-pbf file=data/Hoehendaten_Freizeitkarte_GBR+.osm.pbf \
    --merge --write-pbf tmp/uk-ele.pbf omitmetadata=true

# 2) Split into tiles bounded by the polygon (parallelises mkgmap, saves RAM)
java -Xmx48G -jar bin/splitter-r654/splitter.jar \
     --max-nodes=1200000 --mapid=21140001 \
     --polygon-file=polygons/great-britain.poly \
     --description="GB openfietsnew" \
     --output-dir=tmp/split tmp/uk-ele.pbf

# 3) mkgmap → gmapsupp.img   (fid=2114; uses the precompiled .typ binary, no gmt needed)
java -Xmx62G -jar bin/mkgmap-r4924/mkgmap.jar \
     --read-config=styles/openfietsnew/template.args \
     --family-id=2114 --mapname=21140001 \
     --output-dir=tmp --gmapsupp --tdbfile --max-jobs=20 \
     --style-file=styles/openfietsnew \
     tmp/split/*.pbf styles/typ/openfietsnew.typ
