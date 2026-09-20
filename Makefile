# Build the openfietsnew map for a single area, same pipeline as test.sh but with
# make's incremental rebuilds: each expensive step has an output to track, so re-running
# `make` only reruns osmosis/splitter/mkgmap when their inputs actually changed.

# --- Vendored binaries (matches test.sh; no PATH / system-binary assumptions) ---
OSMOSIS  = bin/osmosis-0.49.2/bin/osmosis
SPLITTER_JAR = bin/splitter-r654/splitter.jar
MKGMAP_JAR = bin/mkgmap-r4924/mkgmap.jar

# --- Input data (matches test.sh) ---
OSM	  = data/united-kingdom-260914.osm.pbf		  # OSM roads
ELEV	 = data/Hoehendaten_Freizeitkarte_GBR+.osm.pbf # contour / elevation
CITIES   = data/cities15000.zip						# place-name labels (blank if missing)
SEA	  = data/sea-latest.zip						 # coastlines (blank if missing)
BOUNDS   = data/bounds-latest.zip					   # addresses / admin boundaries / POIs

# --- Area / style identity ---
AREA	 = great-britain
# AREA	 = london-bridge-kidbrooke
POLYGON  = polygons/$(AREA).poly
STYLE	= openfietsnew

ifeq ($(strip $(shell test -s polygons/$(AREA).poly.fid && echo yes)),yes)
fid = $(shell cat polygons/$(AREA).poly.fid)
else
$(error Missing .poly.fid for "$(AREA)": create it with a unique two-digit code, e.g. "echo 14 > polygons/$(AREA).poly.fid")
endif
MAPID	= 21$(fid)0001								  # --mapid  (e.g. 21140001)
FAMILY   = 21$(fid)									  # --family-id (e.g. 2114)

tmp := tmp

# --- Intermediate / final outputs tracked by make for incremental rebuilds ---
UK_ELE  := $(tmp)/uk-ele.pbf
MAPIMG  := maps/$(AREA)/gmapsupp.img

# Compiled TYP styles from source: every *.typ.txt under styles/typ is compiled automatically,
# so a new .prj can never go stale and there's nothing to keep in sync with mkgmap.
TYP_STYLES := $(patsubst styles/typ/%.typ.txt,styles/typ/%.typ,$(wildcard styles/typ/*.typ.txt))

.PHONY: all build 
.DEFAULT_GOAL := all

all: build
build: $(MAPIMG)

# --- TYP: text source -> binary .typ (no gmt needed) ---
styles/typ/%.typ: styles/typ/%.typ.txt
	java -cp $(MKGMAP_JAR) uk.me.parabola.mkgmap.main.TypCompiler $< $@

# --- (1) Merge OSM + elevation into a single PBF ---
# Incremental on the two input files; rerun only when they change.
$(UK_ELE): $(OSM) $(ELEV)
	mkdir -p tmp
	$(OSMOSIS) --read-pbf file=$(OSM) \
		--read-pbf file=$(ELEV) --merge \
		--write-pbf $@ omitmetadata=true

# --- (2) Split into tiles bounded by the polygon ---
# Parallelises mkgmap and saves RAM. Guarded by a stamp so splitter only reruns when its
# inputs change; test.sh always re-ran it, this does not.
tmp/$(AREA)/split/__split.done: $(UK_ELE) $(POLYGON) $(CITIES) $(SEA)
	mkdir -p $(tmp)/$(AREA)/split
	java -Xmx48G -jar $(SPLITTER_JAR) \
		 --max-nodes=1200000 --mapid=$(MAPID) \
		 --polygon-file=$(POLYGON) \
		 --geonames-file=$(CITIES) \
		 --precomp-sea=$(SEA) \
		 --description="GB openfietsnewer" \
		 --output-dir=tmp/$(AREA)/split/ $(UK_ELE)
	@touch $@

# --- (3) mkgmap -> gmapsupp.img ---
# Incremental on the split tiles and every style source file, so editing a .prj rebuilds just this.
$(MAPIMG): tmp/$(AREA)/split/__split.done $(TYP_STYLES) \
		   $(shell find styles/$(STYLE) -type f 2>/dev/null) styles/$(STYLE)/template.args
	mkdir -p $(tmp)/$(AREA)/out
	java -Xmx52G -jar $(MKGMAP_JAR) \
		 --read-config=styles/$(STYLE)/template.args \
		 --family-id=$(FAMILY) --mapname=$(MAPID) \
		 --drive-on=left \
		 --output-dir=$(tmp)/$(AREA)/out --gmapsupp --tdbfile --max-jobs=10 \
		 --bounds=$(BOUNDS) \
		 --precomp-sea=$(SEA) \
		 --remove-ovm-work-files \
		 --style-file=styles/$(STYLE) \
		 tmp/$(AREA)/split/*.pbf styles/typ/openfietsnew.typ
	mkdir -p maps/$(AREA)
	mv $(tmp)/$(AREA)/out/gmapsupp.img "$@"


