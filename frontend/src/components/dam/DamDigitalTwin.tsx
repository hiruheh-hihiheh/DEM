import React from 'react';
import type { DamDigitalTwin as DamDigitalTwinData } from '../../types/digitalTwin';
import { DamOverview } from './DamOverview';
import { DamStructural } from './DamStructural';
import { DamReservoir } from './DamReservoir';
import { DamHydrology } from './DamHydrology';
import { DamTerrain } from './DamTerrain';
import { DamEnvironment } from './DamEnvironment';
import { DamExposure } from './DamExposure';
import { DamMonitoring } from './DamMonitoring';
import './DamDigitalTwin.css';

export interface DamDigitalTwinProps {
  data: DamDigitalTwinData;
}

export const DamDigitalTwin: React.FC<DamDigitalTwinProps> = ({ data }) => {
  return (
    <div className="dt-container">
      <DamOverview data={data.identity} />
      <DamStructural data={data.structural} />
      <DamReservoir data={data.reservoir} />
      <DamHydrology data={data.hydrology} />
      <DamTerrain data={data.terrain} />
      <DamEnvironment data={data.environment} />
      <DamExposure data={data.exposure} />
      <DamMonitoring data={data.monitoring} />
    </div>
  );
};