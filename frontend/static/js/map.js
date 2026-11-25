/*
 * The GNSS map (Modem page). Everything comes from the router itself:
 * MapLibre and its fonts and icons (/static/vendor/map), the built-in
 * world map (Natural Earth countries) and, when one was downloaded, the
 * detailed OpenStreetMap region (/maps/region.pmtiles, read in pieces).
 *
 *   const map = await ChardsoftMap.create(element, { region });
 *   map.setPosition(position);  map.setTrack(points);  map.destroy();
 */

window.ChardsoftMap = {

    VENDOR: "/static/vendor/map",

    loading: null,


    loadScript(src) {

        return new Promise((resolve, reject) => {

            if ([...document.scripts].some(s => s.src.endsWith(src))) return resolve();

            const script = document.createElement("script");
            script.src = src;
            script.onload = resolve;
            script.onerror = () => reject(new Error(`Could not load ${src}`));
            document.head.appendChild(script);

        });

    },


    /** MapLibre and the PMTiles reader, once per page load. */
    load() {

        if (this.loading) return this.loading;

        this.loading = (async () => {

            if (!document.getElementById("maplibreCss")) {
                const link = document.createElement("link");
                link.id = "maplibreCss";
                link.rel = "stylesheet";
                link.href = `${this.VENDOR}/maplibre-gl.css`;
                document.head.appendChild(link);
            }

            await this.loadScript(`${this.VENDOR}/maplibre-gl.js`);
            await this.loadScript(`${this.VENDOR}/pmtiles.js`);
            await this.loadScript(`${this.VENDOR}/basemaps.js`);

            const protocol = new pmtiles.Protocol();
            maplibregl.addProtocol("pmtiles", protocol.tile);

        })();

        return this.loading;

    },


    isDark() {

        const theme = document.documentElement.dataset.theme;

        if (theme) return theme === "dark";

        return window.matchMedia("(prefers-color-scheme: dark)").matches;

    },


    token(name, fallback) {

        return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

    },


    /** The whole style: world underneath, the detailed region on top, our layers last. */
    style(region) {

        const origin = location.origin;
        const dark = this.isDark();
        const flavor = dark ? "dark" : "light";

        const water = dark ? "#1b2633" : "#cfe0ee";
        const land = dark ? "#2b3440" : "#f2efe9";
        const border = dark ? "#4c5867" : "#b9b2a6";

        const sources = {
            "chardsoft-world": {
                type: "geojson",
                data: `${origin}${this.VENDOR}/countries.geojson`,
                attribution: "Natural Earth"
            },
            "chardsoft-track": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
            "chardsoft-me": { type: "geojson", data: { type: "FeatureCollection", features: [] } }
        };

        let layers = [
            { id: "chardsoft-water", type: "background", paint: { "background-color": water } },
            { id: "chardsoft-world-land", type: "fill", source: "chardsoft-world", paint: { "fill-color": land } },
            { id: "chardsoft-world-border", type: "line", source: "chardsoft-world", paint: { "line-color": border, "line-width": 0.6 } }
        ];

        // Country names on the world map, where no detailed map labels them.
        if (!region) {
            layers.push({
                id: "chardsoft-world-names", type: "symbol", source: "chardsoft-world", minzoom: 2,
                layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 11 },
                paint: {
                    "text-color": dark ? "#9aa6b4" : "#6b6358",
                    "text-halo-color": land, "text-halo-width": 1
                }
            });
        }

        if (region) {

            sources.protomaps = {
                type: "vector",
                url: `pmtiles://${origin}/maps/region.pmtiles`,
                attribution: '<a href="https://protomaps.com" target="_blank" rel="noopener">Protomaps</a> © <a href="https://openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
            };

            // Its own background would hide the world outside the region.
            layers = layers.concat(
                basemaps.layers("protomaps", basemaps.namedFlavor(flavor), { lang: "en" })
                    .filter(layer => layer.type !== "background")
            );

        }

        const accent = this.token("--color-accent", "#2dd4bf");

        layers.push(
            {
                id: "chardsoft-track", type: "line", source: "chardsoft-track",
                layout: { "line-join": "round", "line-cap": "round" },
                paint: { "line-color": accent, "line-width": 3, "line-opacity": 0.8 }
            },
            {
                id: "chardsoft-me-halo", type: "circle", source: "chardsoft-me",
                paint: { "circle-radius": 14, "circle-color": accent, "circle-opacity": 0.25 }
            },
            {
                id: "chardsoft-me", type: "circle", source: "chardsoft-me",
                paint: {
                    "circle-radius": 6, "circle-color": accent,
                    "circle-stroke-width": 2, "circle-stroke-color": dark ? "#0b1220" : "#ffffff"
                }
            }
        );

        return {
            version: 8,
            glyphs: `${origin}${this.VENDOR}/fonts/{fontstack}/{range}.pbf`,
            sprite: `${origin}${this.VENDOR}/sprites/${flavor}`,
            sources,
            layers
        };

    },


    async create(element, { region = null } = {}) {

        await this.load();

        const self = this;

        const view = region?.bbox
            ? { bounds: region.bbox, fitBoundsOptions: { padding: 20 } }
            : { center: [10, 30], zoom: 0.8 };

        const map = new maplibregl.Map({
            container: element,
            style: this.style(region),
            attributionControl: { compact: true },
            // The detailed map ends where its data ends.
            maxZoom: region ? Math.max(region.maxzoom + 2, 6) : 7,
            ...view
        });

        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

        let position = null;
        let track = [];
        let centered = false;

        // A broken detailed map must not keep the page waiting.
        const ready = Promise.race([
            new Promise(resolve => map.once("load", resolve)),
            new Promise(resolve => setTimeout(resolve, 5000))
        ]);

        // Our layers' sources; the style may be loading or have failed.
        const source = id => {
            try {
                return map.getSource(id);
            } catch {
                return null;
            }
        };

        const draw = () => {

            source("chardsoft-me")?.setData({
                type: "FeatureCollection",
                features: position ? [{
                    type: "Feature",
                    geometry: { type: "Point", coordinates: [position.longitude, position.latitude] },
                    properties: {}
                }] : []
            });

            source("chardsoft-track")?.setData({
                type: "FeatureCollection",
                features: track.length > 1 ? [{
                    type: "Feature",
                    geometry: { type: "LineString", coordinates: track.map(p => [p[2], p[1]]) },
                    properties: {}
                }] : []
            });

        };

        // Light / dark follows the page.
        const observer = new MutationObserver(() => {
            map.setStyle(self.style(region));
            map.once("styledata", draw);
        });

        observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

        await ready;

        return {

            map,

            setPosition(next) {

                position = next;
                draw();

                // The first position: there, close enough to see the place.
                if (next && !centered) {
                    centered = true;
                    this.center();
                }

            },

            center() {

                if (!position) return;

                const inside = region?.bbox
                    && position.longitude >= region.bbox[0] && position.longitude <= region.bbox[2]
                    && position.latitude >= region.bbox[1] && position.latitude <= region.bbox[3];

                map.flyTo({
                    center: [position.longitude, position.latitude],
                    zoom: inside ? Math.min(region.maxzoom, 14) : 5,
                    duration: 600
                });

            },

            setTrack(points) {

                track = points || [];
                draw();

            },

            destroy() {

                observer.disconnect();
                map.remove();

            }

        };

    }

};
