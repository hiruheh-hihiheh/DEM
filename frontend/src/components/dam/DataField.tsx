import React from 'react';

export interface DataFieldProps {
  label: string;
  value: string | number | null | undefined;
  unit?: string;
  status?: 'normal' | 'warning' | 'critical' | 'info';
  source?: string;
  isText?: boolean;
}

export const DataField: React.FC<DataFieldProps> = ({ 
  label, 
  value, 
  unit, 
  status = 'normal', 
  source,
  isText = false
}) => {
  const isEmpty = value === null || value === undefined || value === '';
  const displayValue = isEmpty ? 'Not available' : value;

  return (
    <div className={`dt-field dt-field--${status} ${isText ? 'dt-field--text' : ''}`}>
      <div className="dt-field__header">
        <span className="dt-field__label">{label}</span>
        {source && <span className="dt-field__source">{source}</span>}
      </div>
      <div className="dt-field__value">
        <span className={isEmpty ? 'dt-field__na' : ''}>{displayValue}</span>
        {unit && !isEmpty && <span className="dt-field__unit">{unit}</span>}
      </div>
    </div>
  );
};