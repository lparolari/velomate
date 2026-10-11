#!/bin/sh
# Grafana entrypoint wrapper — selects the default map basemap from
# VELOMATE_MAP_PROVIDER, then hands off to the stock Grafana entrypoint.
#
# The geomap panels don't pin a basemap, so they use Grafana's default
# ([geomap] default_baselayer_config). Grafana's built-in default is CARTO,
# which now requires an API key and serves "API KEY REQUIRED" placeholder
# tiles without one — hence OpenStreetMap is the VeloMate default.
#
#   osm            OpenStreetMap standard tiles (default, no key)
#   carto          CARTO Voyager      (needs CARTO_API_KEY)
#   carto-light    CARTO Positron     (needs CARTO_API_KEY)
#   carto-dark     CARTO Dark Matter  (needs CARTO_API_KEY)
#   custom         any XYZ tile URL in VELOMATE_MAP_TILE_URL
#                  ({z}/{x}/{y} placeholders), attribution in
#                  VELOMATE_MAP_ATTRIBUTION
#
# An explicit GF_GEOMAP_DEFAULT_BASELAYER_CONFIG always wins.

xyz() {
  printf '{"type":"xyz","config":{"url":"%s","attribution":"%s"}}' "$1" "$2"
}

carto() {
  if [ -z "$CARTO_API_KEY" ]; then
    echo "[velomate] VELOMATE_MAP_PROVIDER=$VELOMATE_MAP_PROVIDER needs CARTO_API_KEY — falling back to osm" >&2
    echo '{"type":"osm-standard","config":{}}'
    return
  fi
  xyz "https://basemaps.cartocdn.com/rastertiles/$1/{z}/{x}/{y}.png?key=$CARTO_API_KEY" \
    '© <a href=\"https://carto.com/attributions\">CARTO</a> © <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors'
}

if [ -z "$GF_GEOMAP_DEFAULT_BASELAYER_CONFIG" ]; then
  provider="${VELOMATE_MAP_PROVIDER:-osm}"
  case "$provider" in
    osm)         cfg='{"type":"osm-standard","config":{}}' ;;
    carto)       cfg=$(carto voyager) ;;
    carto-light) cfg=$(carto light_all) ;;
    carto-dark)  cfg=$(carto dark_all) ;;
    custom)
      if [ -z "$VELOMATE_MAP_TILE_URL" ]; then
        echo "[velomate] VELOMATE_MAP_PROVIDER=custom needs VELOMATE_MAP_TILE_URL — falling back to osm" >&2
        cfg='{"type":"osm-standard","config":{}}'
      else
        cfg=$(xyz "$VELOMATE_MAP_TILE_URL" "${VELOMATE_MAP_ATTRIBUTION:-}")
      fi
      ;;
    *)
      echo "[velomate] unknown VELOMATE_MAP_PROVIDER='$provider' — falling back to osm" >&2
      cfg='{"type":"osm-standard","config":{}}'
      ;;
  esac
  export GF_GEOMAP_DEFAULT_BASELAYER_CONFIG="$cfg"
  echo "[velomate] map provider: $provider" >&2
fi

exec /run.sh "$@"
