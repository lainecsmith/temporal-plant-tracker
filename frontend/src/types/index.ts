export interface CareRanges {
  soil_moisture_min: number;
  soil_moisture_max: number;
  temperature_min: number;
  temperature_max: number;
  air_humidity_min: number;
  air_humidity_max: number;
  light_lux_min: number | null;
  light_lux_max: number | null;
}

export interface SensorReadings {
  soil_moisture: number | null;
  temperature: number | null;
  air_humidity: number | null;
  light_lux: number | null;
  timestamp: string;
}

export type PlantStatus = "ok" | "warning" | "unknown" | "dead" | "given_away";

/** Statuses that end the workflow — plant is removed from the active list */
export const TERMINAL_STATUSES: ReadonlySet<PlantStatus> = new Set(["dead", "given_away"]);

export interface PlantState {
  plant_id: string;
  name: string;
  species: string;
  care_ranges: CareRanges;
  care_ranges_source: "openplantbook" | "ai" | "manual" | "unknown";
  /** Per-metric AI reasoning strings — only present when care_ranges_source === "ai" */
  care_ranges_reasoning: Record<string, string> | null;
  // Legacy single-entity association
  sensor_entity_id: string | null;
  // Device-level association (preferred)
  sensor_device_id: string | null;
  sensor_device_name: string | null;
  sensor_entities: Record<string, string> | null; // device_class -> entity_id
  last_readings: SensorReadings | null;
  out_of_range_fields: string[];
  status: PlantStatus;
  created_at: string;
  last_checked_at: string | null;

  /**
   * Last-error fields — desired-vs-applied pattern.
   * Each is null when the last operation succeeded (or was never attempted).
   * Set to a human-readable error string when the operation last failed.
   * Automatically cleared when the operation next succeeds.
   */
  /** Set when associate_device receives an empty/invalid entity map. */
  last_association_error: string | null;
  /** Set when get_sensor_readings fails (bad device ID, HA unreachable, etc.). */
  last_sensor_read_error: string | null;
  /** Set when the care-ranges fetch (OpenPlantbook + AI fallback) fails entirely. */
  last_care_ranges_fetch_error: string | null;
  /** Set when trigger_ha_alert or clear_ha_alert_light fails. */
  last_alert_error: string | null;
}

/** Legacy entity-level sensor (used by /sensors endpoint) */
export interface HASensor {
  entity_id: string;
  friendly_name: string;
  state: string | null;
}

/** A single sensor entity belonging to a plant sensor device */
export interface HADeviceEntity {
  entity_id: string;
  friendly_name: string;
  device_class: string | null; // "moisture" | "temperature" | "humidity" | "illuminance" | "battery"
}

/** A Home Assistant device containing one or more plant sensor entities */
export interface HADevice {
  device_id: string;
  name: string;
  manufacturer: string | null;
  model: string | null;
  area_name: string | null;
  entities: HADeviceEntity[];
}
