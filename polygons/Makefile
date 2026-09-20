
countries =
# countries += albania
# countries += andorra
# countries += austria
# countries += belgium
# countries += bosnia-herzegovina
# countries += bulgaria
# countries += croatia
# countries += czech-republic
# countries += denmark
# countries += france
# countries += germany
countries += great-britain
# countries += greece
# countries += hungary
# countries += ireland-and-northern-ireland
# countries += italy
# countries += liechtenstein
# countries += luxembourg
# countries += macedonia
# countries += monaco
# countries += montenegro
# countries += netherlands
# countries += poland
# countries += portugal
# countries += romania
# countries += serbia
# countries += slovakia
# countries += slovenia
# countries += spain
# countries += switzerland

%.poly:
	wget -N -O $@.tmp http://download.geofabrik.de/europe/$@
	mv $@.tmp $@

default:
	@echo no default
	@echo perhaps make fetch
	@echo perhaps make build

fetch: $(countries:=.poly)

build:
	for country in $(countries); do make area=$$country -C .. build; done



