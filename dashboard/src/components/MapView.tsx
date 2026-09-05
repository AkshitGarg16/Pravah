import { useEffect, useMemo, useRef, useState } from 'react'
import Map, {
  Marker,
  Popup,
  Source,
  Layer,
  NavigationControl,
  ScaleControl,
  type MapRef,
  type MapProps,
  type MapLayerMouseEvent,
  type LineLayer,
} from 'react-map-gl'
import type { FeatureCollection } from 'geojson'
import { useDashboardStore } from '../store/useDashboardStore'
import { useLiveStore } from '../store/useLiveStore'
import type {
  SuggestedSite,
  NetworkSegment,
  NetworkJunction,
  LiveSegment,
  LiveJunction,
} from '../types'
import { getCongestionColor, getCongestionLabel } from '../utils/congestion'
import { CONGESTION_COLOR } from '../utils/liveLights'
import { getSiteColor, kindShort, getPriority, priorityLabel } from '../utils/sites'

// Only the Mapbox basemap needs this. The default basemap is OpenStreetMap
// raster tiles, which need no token, so an empty value is a normal state
// rather than a misconfiguration -- see the guard further down.
const MAPBOX_TOKEN = (import.meta.env.VITE_MAPBOX_TOKEN as string | undefined) ?? ''

const SEGMENT_LAYER_ID = 'segments-line'

// Raster basemap straight off the OpenStreetMap tile server. Segment geometries
// are traced from the same OSM ways, so they line up with the drawn roads.
const OSM_STYLE: NonNullable<MapProps['mapStyle']> = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm-tiles', type: 'raster', source: 'osm' }],
}

const MAPBOX_STYLE = 'mapbox://styles/mapbox/light-v11'

const lineLayer: LineLayer = {
  id: SEGMENT_LAYER_ID,
  type: 'line',
  source: 'segments',
  layout: {
    'line-cap': 'round',
    'line-join': 'round',
  },
  paint: {
    'line-width': ['interpolate', ['linear'], ['zoom'], 12, 2.5, 15, 5, 18, 8],
    'line-opacity': ['case', ['==', ['get', 'status'], 'live'], 0.9, 0.55],
    // congestion is -1 when the segment has no live observation yet
    'line-color': [
      'step',
      ['get', 'congestion'],
      '#B8C6D2',
      0,
      '#2FA98C',
      0.25,
      '#E8A33D',
      0.5,
      '#E4674F',
    ],
  },
}

// White casing under the coloured line so segments stay readable over OSM tiles.
const lineCasingLayer: LineLayer = {
  id: 'segments-casing',
  type: 'line',
  source: 'segments',
  layout: { 'line-cap': 'round', 'line-join': 'round' },
  paint: {
    'line-width': ['interpolate', ['linear'], ['zoom'], 12, 4.5, 15, 9, 18, 13],
    'line-color': '#FFFFFF',
    'line-opacity': 0.9,
  },
}

const phaseFromLive: Record<string, string> = {
  green: '#2FA98C',
  amber: '#E8A33D',
  red: '#E4674F',
  off: '#9AABB8',
}

interface HoverInfo {
  longitude: number
  latitude: number
  segment: NetworkSegment
  live: LiveSegment | undefined
}

export default function MapView() {
  const network = useLiveStore((s) => s.network)
  const liveSegments = useLiveStore((s) => s.segments)
  const liveJunctions = useLiveStore((s) => s.junctions)
  const feedStatus = useLiveStore((s) => s.status)
  const networkSource = useLiveStore((s) => s.networkSource)
  const lastUpdate = useLiveStore((s) => s.lastUpdate)
  const initFeed = useLiveStore((s) => s.init)

  const viewState = useDashboardStore((s) => s.viewState)
  const setViewState = useDashboardStore((s) => s.setViewState)
  const flyTarget = useDashboardStore((s) => s.flyTarget)
  const basemap = useDashboardStore((s) => s.basemap)
  const setBasemap = useDashboardStore((s) => s.setBasemap)
  const infraMode = useDashboardStore((s) => s.infraMode)
  const sites = useDashboardStore((s) => s.sites)
  const selectedSiteId = useDashboardStore((s) => s.selectedSiteId)
  const selectSite = useDashboardStore((s) => s.selectSite)

  const mapRef = useRef<MapRef | null>(null)
  const [hoverInfo, setHoverInfo] = useState<HoverInfo | null>(null)
  const [junctionHover, setJunctionHover] = useState<string | null>(null)
  const [selectedJunctionId, setSelectedJunctionId] = useState<string | null>(null)

  // Load the canonical network and open the live feed once.
  useEffect(() => initFeed(), [initFeed])

  // Centre on the canonical network the first time it lands.
  useEffect(() => {
    if (network && mapRef.current) {
      mapRef.current.flyTo({
        center: [network.center.lng, network.center.lat],
        zoom: 14.2,
        duration: 600,
      })
    }
  }, [network])

  // Animate to the requested location whenever a new flyTo request arrives.
  useEffect(() => {
    if (flyTarget && mapRef.current) {
      mapRef.current.flyTo({
        center: [flyTarget.longitude, flyTarget.latitude],
        zoom: flyTarget.zoom,
        duration: 800,
        essential: true,
      })
    }
  }, [flyTarget])

  // Geometry is static OSM; only the paint properties change as data arrives.
  const segmentGeoJSON = useMemo<FeatureCollection>(
    () => ({
      type: 'FeatureCollection',
      features: (network?.segments ?? []).map((seg) => {
        const live = liveSegments[seg.id]
        return {
          type: 'Feature' as const,
          properties: {
            id: seg.id,
            status: live?.status ?? 'no-data',
            congestion: live?.congestion ?? -1,
          },
          geometry: {
            type: 'LineString' as const,
            // coords are [lat, lng]; GeoJSON wants [lng, lat]
            coordinates: seg.coords.map(([lat, lng]) => [lng, lat]),
          },
        }
      }),
    }),
    [network, liveSegments],
  )

  // Only junctions that are signalised or actually reporting get a marker.
  const shownJunctions = useMemo(() => {
    if (!network) return []
    return network.junctions.filter(
      (j) => j.signalised || liveJunctions[j.id]?.status === 'live',
    )
  }, [network, liveJunctions])

  const segmentNames = useMemo(() => {
    const map: Record<string, string> = {}
    for (const s of network?.segments ?? []) map[s.id] = s.name
    return map
  }, [network])

  const activeJunction = useMemo(() => {
    const id = junctionHover ?? selectedJunctionId
    if (!id || !network) return null
    return network.junctions.find((j) => j.id === id) ?? null
  }, [junctionHover, selectedJunctionId, network])

  const liveCount = useMemo(
    () => Object.values(liveSegments).filter((s) => s.status === 'live').length,
    [liveSegments],
  )

  const selectedSite = sites.find((s) => s.id === selectedSiteId) ?? null

  const onHover = (e: MapLayerMouseEvent) => {
    const feature = e.features && e.features[0]
    if (feature && network) {
      const id = feature.properties?.id as string
      const seg = network.segments.find((s) => s.id === id)
      if (seg) {
        setHoverInfo({
          longitude: e.lngLat.lng,
          latitude: e.lngLat.lat,
          segment: seg,
          live: liveSegments[seg.id],
        })
        return
      }
    }
    setHoverInfo(null)
  }

  if (basemap === 'mapbox' && !MAPBOX_TOKEN) {
    return (
      <div
        className="flex items-center justify-center bg-surface"
        style={{ gridArea: 'map' }}
      >
        <div className="max-w-sm text-center px-6">
          <p className="text-sm font-semibold text-ink mb-1">Mapbox token missing</p>
          <p className="text-xs text-muted leading-relaxed">
            Set <code className="text-teal">VITE_MAPBOX_TOKEN</code> in a{' '}
            <code className="text-teal">.env</code> file (see{' '}
            <code className="text-teal">.env.example</code>) and restart the dev server, or
            switch back to the OpenStreetMap basemap, which needs no token.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="relative" style={{ gridArea: 'map' }}>
      <Map
        ref={mapRef}
        initialViewState={viewState}
        onMove={(e) =>
          setViewState({
            longitude: e.viewState.longitude,
            latitude: e.viewState.latitude,
            zoom: e.viewState.zoom,
          })
        }
        mapboxAccessToken={MAPBOX_TOKEN}
        mapStyle={basemap === 'osm' ? OSM_STYLE : MAPBOX_STYLE}
        style={{ width: '100%', height: '100%' }}
        interactiveLayerIds={[SEGMENT_LAYER_ID]}
        onMouseMove={onHover}
        onMouseLeave={() => setHoverInfo(null)}
      >
        <NavigationControl position="bottom-right" showCompass={false} />
        <ScaleControl position="bottom-left" />

        <Source id="segments" type="geojson" data={segmentGeoJSON}>
          <Layer {...lineCasingLayer} />
          <Layer {...lineLayer} />
        </Source>

        {/* Segment hover popup */}
        {hoverInfo && (
          <Popup
            longitude={hoverInfo.longitude}
            latitude={hoverInfo.latitude}
            closeButton={false}
            closeOnClick={false}
            anchor="bottom"
            offset={12}
          >
            <SegmentPopup segment={hoverInfo.segment} live={hoverInfo.live} />
          </Popup>
        )}

        {/* Junction markers — colour is the live signal phase, grey when silent */}
        {shownJunctions.map((j) => {
          const live = liveJunctions[j.id]
          const phases = live?.phases ? Object.values(live.phases) : []
          const dominant =
            live?.phase ??
            (phases.includes('green')
              ? 'green'
              : phases.includes('amber')
                ? 'amber'
                : phases.length
                  ? 'red'
                  : undefined)
          const color = dominant ? (phaseFromLive[dominant] ?? '#9AABB8') : '#B8C6D2'
          const size = j.approaches.length >= 4 ? 16 : 12
          const jammed = live?.metrics?.spillback ||
            live?.metrics?.congestion === 'jammed' ||
            live?.metrics?.congestion === 'heavy'
          const isActive = j.id === selectedJunctionId
          return (
            <Marker key={j.id} longitude={j.lng} latitude={j.lat} anchor="center">
              <div
                className="relative flex items-center justify-center cursor-pointer"
                style={{ opacity: infraMode ? 0.45 : 1 }}
                onMouseEnter={() => setJunctionHover(j.id)}
                onMouseLeave={() => setJunctionHover(null)}
                onClick={() => setSelectedJunctionId(isActive ? null : j.id)}
              >
                {isActive && (
                  <div
                    className="absolute rounded-full"
                    style={{
                      width: size + 12,
                      height: size + 12,
                      backgroundColor: '#18A0A8',
                      opacity: 0.3,
                    }}
                  />
                )}
                {live?.ev && (
                  <div
                    className="absolute rounded-full animate-ev-dot"
                    style={{ width: size + 10, height: size + 10, backgroundColor: '#E4674F', opacity: 0.35 }}
                  />
                )}
                <div
                  className="rounded-full shadow-sm relative"
                  style={{
                    width: size,
                    height: size,
                    backgroundColor: color,
                    // A red ring means the queue is not clearing, whatever
                    // colour the signal is currently showing.
                    border: jammed ? '3px solid #B3341C' : '2px solid #FFFFFF',
                  }}
                />
              </div>
            </Marker>
          )
        })}

        {/* Junction hover / selection popup */}
        {activeJunction && (
          <Popup
            longitude={activeJunction.lng}
            latitude={activeJunction.lat}
            closeButton={false}
            closeOnClick={false}
            anchor="bottom"
            offset={16}
          >
            <JunctionPopup
              junction={activeJunction}
              live={liveJunctions[activeJunction.id]}
              segmentNames={segmentNames}
            />
          </Popup>
        )}

        {/* Suggested infrastructure sites — pinged in Infrastructure Development Mode */}
        {infraMode &&
          sites.map((site, i) => {
            const color = getSiteColor(site.score)
            const isActive = site.id === selectedSiteId
            return (
              <Marker
                key={site.id}
                longitude={site.lng}
                latitude={site.lat}
                anchor="center"
                onClick={(e) => {
                  e.originalEvent.stopPropagation()
                  selectSite(site.id)
                }}
              >
                <div className="relative flex items-center justify-center cursor-pointer">
                  {/* Expanding ping ring */}
                  <span
                    className="absolute rounded-full animate-site-ping"
                    style={{
                      width: 34,
                      height: 34,
                      border: `2px solid ${color}`,
                      animationDelay: `${(i % 4) * 0.4}s`,
                    }}
                  />
                  {isActive && (
                    <span
                      className="absolute rounded-full"
                      style={{ width: 30, height: 30, backgroundColor: color, opacity: 0.22 }}
                    />
                  )}
                  <span
                    className="relative rounded-full flex items-center justify-center text-white font-semibold shadow"
                    style={{
                      width: 20,
                      height: 20,
                      fontSize: 10,
                      backgroundColor: color,
                      border: '2px solid #FFFFFF',
                    }}
                  >
                    {i + 1}
                  </span>
                </div>
              </Marker>
            )
          })}

        {/* Selected site detail popup */}
        {infraMode && selectedSite && (
          <Popup
            longitude={selectedSite.lng}
            latitude={selectedSite.lat}
            closeButton={false}
            closeOnClick={false}
            anchor="bottom"
            offset={20}
          >
            <SitePopup site={selectedSite} />
          </Popup>
        )}

      </Map>

      {/* Feed status */}
      <div className="absolute top-3 left-3 bg-card/95 border border-edge rounded-md shadow-sm px-2.5 py-1.5 flex items-center gap-2">
        <span
          className={`rounded-full ${feedStatus === 'live' ? 'animate-pulse-dot' : ''}`}
          style={{
            width: 7,
            height: 7,
            backgroundColor:
              feedStatus === 'live' ? '#2FA98C' : feedStatus === 'connecting' ? '#E8A33D' : '#E4674F',
          }}
        />
        <span className="text-[11px] font-medium text-ink">
          {feedStatus === 'live'
            ? `Live feed · ${liveCount}/${network?.segments.length ?? 0} segments`
            : feedStatus === 'connecting'
              ? 'Connecting to feed…'
              : 'Feed offline'}
        </span>
        <span className="text-[10px] text-faint">
          {lastUpdate
            ? `${Math.max(0, Math.round((Date.now() - lastUpdate) / 1000))}s ago`
            : networkSource === 'bundle'
              ? 'OSM extract (bundled)'
              : 'awaiting data'}
        </span>
      </div>

      {/* Basemap switcher */}
      <div className="absolute top-3 right-3 bg-card border border-edge rounded-md shadow-sm flex overflow-hidden">
        {(['osm', 'mapbox'] as const).map((b) => (
          <button
            key={b}
            type="button"
            onClick={() => setBasemap(b)}
            className={`text-[11px] font-medium px-2.5 py-1 transition-colors ${
              basemap === b ? 'bg-teal text-white' : 'text-muted hover:text-ink'
            }`}
          >
            {b === 'osm' ? 'OSM' : 'Mapbox'}
          </button>
        ))}
      </div>

      {/* Legend */}
      <div className="absolute bottom-3 left-3 bg-card/95 border border-edge rounded-md shadow-sm px-2.5 py-2">
        <div className="text-[9px] uppercase tracking-widest text-faint font-semibold mb-1">
          {infraMode ? 'Model priority' : 'Congestion'}
        </div>
        <div className="flex flex-col gap-1">
          {(infraMode
            ? [
                ['#6B3FA0', 'High priority'],
                ['#18A0A8', 'Medium'],
                ['#9AABB8', 'Watchlist'],
              ]
            : [
                ['#2FA98C', 'Good'],
                ['#E8A33D', 'Moderate'],
                ['#E4674F', 'Severe'],
              ]
          ).map(([color, label]) => (
            <div key={label} className="flex items-center gap-1.5">
              <span
                className="rounded-full"
                style={{ width: 8, height: 8, backgroundColor: color }}
              />
              <span className="text-[10px] text-muted">{label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function SegmentPopup({ segment, live }: { segment: NetworkSegment; live?: LiveSegment }) {
  const congestion = live?.congestion
  const color = congestion === undefined ? '#9AABB8' : getCongestionColor(congestion)
  return (
    <div className="min-w-[170px]">
      <div className="text-xs font-semibold text-navy">{segment.name}</div>
      <div className="text-[10px] text-muted mb-1.5 capitalize">
        {segment.highway} · {segment.lanes} lanes · {Math.round(segment.length_m)} m
        {segment.oneway ? ' · one-way' : ''}
      </div>

      {live && live.status !== 'no-data' ? (
        <>
          <div className="flex justify-between text-[11px] mb-0.5">
            <span className="text-muted">Speed</span>
            <span className="font-semibold" style={{ color }}>
              {live.speed} km/h
            </span>
          </div>
          <div className="flex justify-between text-[11px] mb-0.5">
            <span className="text-muted">Free flow</span>
            <span className="font-semibold text-ink">{live.free_flow} km/h</span>
          </div>
          <div className="flex justify-between text-[11px] mb-0.5">
            <span className="text-muted">Congestion</span>
            <span className="font-semibold capitalize" style={{ color }}>
              {Math.round((live.congestion ?? 0) * 100)}% ·{' '}
              {getCongestionLabel(live.congestion ?? 0)}
            </span>
          </div>
          {live.count !== undefined && (
            <div className="flex justify-between text-[11px] mb-0.5">
              <span className="text-muted">Vehicles</span>
              <span className="font-semibold text-ink">
                {live.count}
                {live.halting ? ` · ${live.halting} stopped` : ''}
              </span>
            </div>
          )}
          <div className="flex justify-between text-[11px]">
            <span className="text-muted">Source</span>
            <span className="font-semibold text-ink">
              {live.fused_from?.join(' + ') || live.source} · {live.age_s}s
            </span>
          </div>
          {live.status === 'stale' && (
            <div className="text-[10px] text-amber font-medium mt-1">Stale observation</div>
          )}
        </>
      ) : (
        <div className="text-[11px] text-faint">No observation on this segment yet.</div>
      )}

      <div className="text-[9px] text-faint mt-1 border-t border-edge pt-1 break-all">
        {segment.id}
        {segment.tomtom_ids?.length ? (
          <div className="mt-0.5">TomTom {segment.tomtom_ids.join(', ')}</div>
        ) : null}
      </div>
    </div>
  )
}

function JunctionPopup({
  junction,
  live,
  segmentNames,
}: {
  junction: NetworkJunction
  live?: LiveJunction
  segmentNames: Record<string, string>
}) {
  const phases = live?.phases ? Object.entries(live.phases) : []
  const m = live?.metrics ?? {}
  const types = Object.entries(m.vehicle_types ?? {}).filter(([, n]) => n > 0)
  return (
    <div className="min-w-[200px] max-w-[260px]">
      <div className="text-xs font-semibold text-navy">
        {junction.name || (junction.signalised ? 'Signalised junction' : 'Junction')}
      </div>
      <div className="text-[10px] text-muted mb-1.5">
        {junction.approaches.length} approaches
        {live?.program ? ` · program ${live.program}` : ''}
        {m.phase_index !== undefined ? ` · phase ${m.phase_index}` : ''}
      </div>

      {live && live.status !== 'no-data' ? (
        <>
          {phases.map(([seg, phase]) => (
            <div key={seg} className="flex justify-between items-center text-[11px] mb-0.5 gap-2">
              <span className="text-muted truncate">{segmentNames[seg] ?? seg}</span>
              <span
                className="font-semibold capitalize shrink-0"
                style={{ color: phaseFromLive[phase] ?? '#6B7D8E' }}
              >
                {phase}
              </span>
            </div>
          ))}
          {live.time_to_change != null && (
            <div className="flex justify-between text-[11px] mt-1 pt-1 border-t border-edge">
              <span className="text-muted">Next change</span>
              <span className="font-semibold text-teal">{Math.round(live.time_to_change)}s</span>
            </div>
          )}

          {/* Junctions are scored on how full their queues are, not on speed:
              a signalised approach is meant to be stopped part of every cycle. */}
          {m.congestion && (
            <div className="flex justify-between text-[11px]">
              <span className="text-muted">Queue fill</span>
              <span
                className="font-semibold capitalize"
                style={{ color: CONGESTION_COLOR[m.congestion] }}
              >
                {Math.round((m.worst_queue_ratio ?? 0) * 100)}% · {m.congestion}
              </span>
            </div>
          )}
          {m.queue_length_m !== undefined && (
            <div className="flex justify-between text-[11px]">
              <span className="text-muted">Queued</span>
              <span className="font-semibold text-ink">
                {m.queued_vehicles ?? 0} veh · {Math.round(m.queue_length_m)} m
              </span>
            </div>
          )}
          {m.max_waiting_time_s !== undefined && (
            <div className="flex justify-between text-[11px]">
              <span className="text-muted">Longest wait</span>
              <span
                className="font-semibold"
                style={{ color: m.max_waiting_time_s >= 60 ? '#E4674F' : '#16222E' }}
              >
                {Math.round(m.max_waiting_time_s)}s
              </span>
            </div>
          )}
          {(live.hv_count ?? 0) > 0 && (
            <div className="flex justify-between text-[11px]">
              <span className="text-muted">Heavy vehicles</span>
              <span className="font-semibold text-ink">{live.hv_count}</span>
            </div>
          )}
          {types.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {types.map(([type, n]) => (
                <span
                  key={type}
                  className="text-[9px] text-muted bg-surface border border-edge rounded px-1 py-0.5"
                >
                  {n} {type}
                </span>
              ))}
            </div>
          )}
          {m.spillback && (
            <div className="text-[10px] text-coral font-semibold mt-1">
              SPILLBACK — queue has reached the junction upstream
            </div>
          )}
          {live.ev && (
            <div className="text-[10px] text-coral font-semibold mt-1">EV priority active</div>
          )}
        </>
      ) : (
        <div className="text-[11px] text-faint">No signal state reported.</div>
      )}

      <div className="text-[9px] text-faint mt-1 border-t border-edge pt-1 break-all">
        {junction.id}
      </div>
    </div>
  )
}

function SitePopup({ site }: { site: SuggestedSite }) {
  const color = getSiteColor(site.score)
  return (
    <div className="min-w-[170px] max-w-[220px]">
      <div className="flex items-center gap-1.5 mb-0.5">
        <span className="rounded-full" style={{ width: 8, height: 8, backgroundColor: color }} />
        <span className="text-xs font-semibold text-navy">{site.name}</span>
      </div>
      <div className="text-[10px] text-muted mb-1.5 capitalize">
        {kindShort[site.kind]} · {priorityLabel[getPriority(site.score)]}
      </div>
      <div className="flex justify-between text-[11px] mb-0.5">
        <span className="text-muted">Model score</span>
        <span className="font-semibold" style={{ color }}>
          {site.score}
        </span>
      </div>
      <div className="flex justify-between text-[11px] mb-0.5">
        <span className="text-muted">Avg delay</span>
        <span className="font-semibold text-healthy">−{site.delaySaving}%</span>
      </div>
      <div className="flex justify-between text-[11px]">
        <span className="text-muted">Est. capex</span>
        <span className="font-semibold text-ink">{site.cost}</span>
      </div>
    </div>
  )
}
