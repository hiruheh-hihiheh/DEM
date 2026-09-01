import React from 'react';
import { DataSection } from './DataSection';
import { DataField } from './DataField';
import type { DamMonitoringData } from '../../types/digitalTwin';
import { IconSatellite } from './Icons';

export const DamMonitoring: React.FC<{ data: DamMonitoringData }> = ({ data }) => (
  <DataSection title="Monitoring & Sensors" icon={<IconSatellite />}>
    <DataField label="Sensors" value={data.sensors} isText />
    <DataField label="Remote Sensing" value={data.remoteSensing} isText />
    <DataField label="Satellite Obs." value={data.satelliteObservations} isText />
    <DataField label="Historical Incidents" value={data.historicalIncidents} isText />
    <DataField label="Inspection Records" value={data.inspectionRecords} isText />
  </DataSection>
);