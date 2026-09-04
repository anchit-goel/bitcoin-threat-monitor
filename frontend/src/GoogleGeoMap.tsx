import { useEffect, useRef, useState } from 'react';
import { importLibrary, setOptions } from '@googlemaps/js-api-loader';
import type { GeoFlow } from './api';

interface GoogleGeoMapProps {
  flows: GeoFlow[];
  hovered: GeoFlow | null;
  setHovered: (flow: GeoFlow | null) => void;
}

// Country lat/lng coordinates
const COUNTRY_COORDS: Record<string, { lat: number; lng: number }> = {
  'USA': { lat: 38.0, lng: -98.0 },
  'Canada': { lat: 60.0, lng: -96.0 },
  'Mexico': { lat: 24.0, lng: -102.0 },
  'Brazil': { lat: -14.0, lng: -51.0 },
  'UK': { lat: 54.0, lng: -3.0 },
  'Germany': { lat: 51.0, lng: 10.0 },
  'France': { lat: 46.0, lng: 2.0 },
  'Switzerland': { lat: 47.0, lng: 8.0 },
  'Netherlands': { lat: 52.0, lng: 5.0 },
  'Russia': { lat: 60.0, lng: 100.0 },
  'China': { lat: 35.0, lng: 105.0 },
  'India': { lat: 20.0, lng: 78.0 },
  'Japan': { lat: 36.0, lng: 138.0 },
  'South Korea': { lat: 37.0, lng: 128.0 },
  'Singapore': { lat: 1.0, lng: 104.0 },
  'UAE': { lat: 24.0, lng: 54.0 },
  'Nigeria': { lat: 10.0, lng: 8.0 },
  'Australia': { lat: -27.0, lng: 133.0 },
};

// Dark theme map styling matching the dashboard palette
const DARK_MAP_STYLE: google.maps.MapTypeStyle[] = [
  { elementType: 'geometry', stylers: [{ color: '#0f1114' }] },
  { elementType: 'labels.text.stroke', stylers: [{ color: '#0f1114' }] },
  { elementType: 'labels.text.fill', stylers: [{ color: '#6a7f96' }] },
  {
    featureType: 'administrative.locality',
    elementType: 'labels.text.fill',
    stylers: [{ color: '#a8b8ca' }]
  },
  {
    featureType: 'poi',
    elementType: 'labels.text.fill',
    stylers: [{ color: '#3f5162' }]
  },
  {
    featureType: 'road',
    elementType: 'geometry',
    stylers: [{ color: '#171a1f' }]
  },
  {
    featureType: 'road',
    elementType: 'geometry.stroke',
    stylers: [{ color: '#262d38' }]
  },
  {
    featureType: 'road.highway',
    elementType: 'geometry',
    stylers: [{ color: '#252b34' }]
  },
  {
    featureType: 'transit',
    elementType: 'geometry',
    stylers: [{ color: '#171a1f' }]
  },
  {
    featureType: 'water',
    elementType: 'geometry',
    stylers: [{ color: '#0b0d10' }]
  },
  {
    featureType: 'water',
    elementType: 'labels.text.fill',
    stylers: [{ color: '#3f5162' }]
  }
];

export function GoogleGeoMap({ flows, hovered, setHovered }: GoogleGeoMapProps) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<google.maps.Map | null>(null);
  const polylinesRef = useRef<google.maps.Polyline[]>([]);
  const markersRef = useRef<google.maps.Marker[]>([]);
  
  const [apiKey, setApiKey] = useState<string>(
    import.meta.env.VITE_GOOGLE_MAPS_API_KEY || ''
  );
  const [isLoaded, setIsLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Initialize Google Maps API.
  //
  // The installed @googlemaps/js-api-loader (v2) replaced the old `new
  // Loader(...).load()` class API with module-level setOptions() +
  // importLibrary() - the class still exists but every method on it was
  // removed, so the original `.load()` call would fail at runtime, not just
  // at typecheck time.
  useEffect(() => {
    if (!apiKey) return;

    setOptions({ key: apiKey, v: 'weekly' });

    importLibrary('maps')
      .then(() => {
        setIsLoaded(true);
        setLoadError(null);
      })
      .catch((err: unknown) => {
        console.error('Google Maps API failed to load:', err);
        setLoadError('Failed to initialize Google Maps API with provided key.');
        setIsLoaded(false);
      });
  }, [apiKey]);

  // Construct Map, Markers, and Geodesic Polyline Arcs once loaded
  useEffect(() => {
    if (!isLoaded || !mapRef.current || !window.google) return;

    if (!mapInstanceRef.current) {
      mapInstanceRef.current = new window.google.maps.Map(mapRef.current, {
        center: { lat: 25.0, lng: 15.0 },
        zoom: 2.3,
        styles: DARK_MAP_STYLE,
        disableDefaultUI: false,
        zoomControl: true,
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: true,
        backgroundColor: '#0f1114'
      });
    }

    const map = mapInstanceRef.current;

    // Clear previous polylines & markers
    polylinesRef.current.forEach(p => p.setMap(null));
    polylinesRef.current = [];
    markersRef.current.forEach(m => m.setMap(null));
    markersRef.current = [];

    const maxAmount = flows.length ? Math.max(...flows.map(f => f.amount)) : 1;

    // Add country markers
    Object.entries(COUNTRY_COORDS).forEach(([name, coords]) => {
      const involved = flows.some(f => f.from_country === name || f.to_country === name);
      if (!involved) return;

      const marker = new window.google.maps.Marker({
        position: coords,
        map,
        title: name,
        icon: {
          path: window.google.maps.SymbolPath.CIRCLE,
          scale: 6,
          fillColor: '#c9512a',
          fillOpacity: 0.9,
          strokeColor: '#dde7f2',
          strokeWeight: 1.5
        }
      });

      markersRef.current.push(marker);
    });

    // Add curved transaction flow polylines
    flows.forEach((flow) => {
      const from = COUNTRY_COORDS[flow.from_country];
      const to = COUNTRY_COORDS[flow.to_country];
      if (!from || !to) return;

      const strokeColor = flow.risk_score >= 80 ? '#c9512a' : flow.risk_score >= 60 ? '#b87c22' : '#6e8faa';
      const isHovered = hovered === flow;
      const weight = 2 + (flow.amount / maxAmount) * 4;

      const polyline = new window.google.maps.Polyline({
        path: [from, to],
        geodesic: true,
        strokeColor: strokeColor,
        strokeOpacity: isHovered ? 1.0 : 0.65,
        strokeWeight: isHovered ? weight + 3 : weight,
        map: map
      });

      polyline.addListener('mouseover', () => {
        setHovered(flow);
      });

      polyline.addListener('mouseout', () => {
        setHovered(null);
      });

      polylinesRef.current.push(polyline);
    });
  }, [isLoaded, flows, hovered, setHovered]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '480px', borderRadius: '4px', overflow: 'hidden', border: '1px solid #262d38', background: '#0f1114' }}>
      {/* Map Container */}
      <div ref={mapRef} style={{ width: '100%', height: '100%' }} />

      {/* Fallback & API Key Input Bar if no key or loading */}
      {(!apiKey || !isLoaded) && (
        <div style={{
          position: 'absolute', inset: 0, background: 'rgba(15, 17, 20, 0.92)',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          padding: '24px', textAlign: 'center', zIndex: 10
        }}>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: '12px', color: '#c9512a', letterSpacing: '0.08em', marginBottom: '8px' }}>
            REALTIME GOOGLE MAPS API INTEGRATION
          </div>
          <div style={{ fontSize: '15px', fontWeight: 600, color: '#dde7f2', marginBottom: '12px', maxWidth: '480px' }}>
            Enter your Google Maps API Key to activate realtime 3D vector map layers & cross-border transaction flow polylines.
          </div>
          
          <div style={{ display: 'flex', gap: '8px', maxWidth: '460px', width: '100%', marginBottom: '12px' }}>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Paste Google Maps API Key (AIzaSy...)"
              style={{
                flex: 1, background: '#171a1f', border: '1px solid #323b47',
                borderRadius: '3px', padding: '8px 12px', color: '#dde7f2',
                fontFamily: "'DM Mono', monospace", fontSize: '12px', outline: 'none'
              }}
            />
            <button
              onClick={() => setIsLoaded(false)}
              style={{
                background: '#c9512a', border: 'none', borderRadius: '3px',
                padding: '8px 16px', color: '#dde7f2', fontWeight: 600,
                fontSize: '12px', fontFamily: "'Plus Jakarta Sans', sans-serif", cursor: 'pointer'
              }}
            >
              Load Map
            </button>
          </div>

          {loadError && (
            <div style={{ color: '#c9512a', fontFamily: "'DM Mono', monospace", fontSize: '11px', marginTop: '4px' }}>
              {loadError}
            </div>
          )}

          {/* Real-time interactive fallback map preview */}
          <div style={{ width: '100%', height: '240px', marginTop: '12px', borderRadius: '4px', overflow: 'hidden', border: '1px solid #262d38' }}>
            <iframe
              title="Realtime World Map Preview"
              width="100%"
              height="100%"
              style={{ border: 0 }}
              loading="lazy"
              allowFullScreen
              src="https://maps.google.com/maps?q=20,0&z=2&output=embed"
            />
          </div>
        </div>
      )}
    </div>
  );
}
