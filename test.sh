#!/bin/bash


set -e 
# Unmatched globs expand to nothing (so an empty source list skips cleanly instead of
# running against a literal "*.prj" path). Set once here for the whole script.
shopt -s nullglob
 
rm -rf tmp/split 
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
     --description="GB openfietsnewer" \
     --output-dir=tmp/split tmp/uk-ele.pbf

# Compile every TYP style source under styles/typ from text -> binary .typ.
# Globbing instead of a hand-written list means edited types can never go stale: any new
# *.prj added here is compiled automatically, so there's nothing to keep in sync with mkgmap.
for style in styles/typ/*.typ.txt; do
    style="${style%.typ.txt}"
    java -cp bin/mkgmap-r4924/mkgmap.jar uk.me.parabola.mkgmap.main.TypCompiler "${style}.typ.txt" "${style}.typ" 
done

rm -f tmp/g
mkdir -p tmp/g

# 3) mkgmap → gmapsupp.img   (fid=2114; binary .typ compiled from source above, no gmt needed)
#    bounds -> addresses / administrative boundaries / derived POIs. Missing before = incomplete map.
java -Xmx52G -jar bin/mkgmap-r4924/mkgmap.jar \
     --read-config=styles/openfietsnew/template.args \
     --family-id=2114 --mapname=21140001 \
     --drive-on=left \
     --output-dir=tmp/g --gmapsupp --tdbfile --max-jobs=10 \
     --bounds=data/bounds-latest.zip \
     --precomp-sea=data/sea-latest.zip \
     --remove-ovm-work-files \
     --style-file=styles/openfietsnew \
     tmp/split/*.pbf styles/typ/openfietsnew.typ




