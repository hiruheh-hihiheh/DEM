import React from 'react';

export interface DataSectionProps {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export const DataSection: React.FC<DataSectionProps> = ({ title, icon, children, className }) => {
  return (
    <section className={`dt-section ${className || ''}`}>
      <header className="dt-section__header">
        {icon && <span className="dt-section__icon">{icon}</span>}
        <h3 className="dt-section__title">{title}</h3>
      </header>
      <div className="dt-section__content">
        {children}
      </div>
    </section>
  );
};