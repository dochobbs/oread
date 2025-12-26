import React, { useState } from 'react';

const TimeTravelMockup = () => {
  const [currentAge, setCurrentAge] = useState(120); // 10 years in months
  const [viewMode, setViewMode] = useState('overview');
  const [showTimeline, setShowTimeline] = useState(true);
  
  // Simulated patient data
  const patient = {
    name: "Olivia Garcia",
    currentAgeMonths: 120,
    dob: "2015-02-22",
    sex: "Female"
  };
  
  // Simulated snapshots with disease arc (Atopic March)
  const snapshots = [
    { ageMonths: 0, label: "Birth", conditions: [], meds: [], event: null },
    { ageMonths: 4, label: "4mo", conditions: ["Eczema"], meds: ["Hydrocortisone"], event: "Eczema onset", isKeyMoment: true },
    { ageMonths: 12, label: "1y", conditions: ["Eczema", "Egg allergy"], meds: ["Hydrocortisone", "EpiPen"], event: "Food allergy diagnosed", isKeyMoment: true },
    { ageMonths: 24, label: "2y", conditions: ["Eczema", "Egg allergy"], meds: ["Hydrocortisone", "EpiPen"], event: null },
    { ageMonths: 36, label: "3y", conditions: ["Eczema", "Egg allergy", "Reactive airway"], meds: ["Hydrocortisone", "EpiPen", "Albuterol PRN"], event: "First wheezing episode", isKeyMoment: true },
    { ageMonths: 48, label: "4y", conditions: ["Eczema", "Egg allergy", "Asthma - intermittent"], meds: ["Hydrocortisone", "EpiPen", "Albuterol PRN"], event: "Asthma diagnosis", isKeyMoment: true },
    { ageMonths: 60, label: "5y", conditions: ["Eczema (mild)", "Egg allergy", "Asthma - intermittent"], meds: ["EpiPen", "Albuterol PRN"], event: "Eczema improving" },
    { ageMonths: 72, label: "6y", conditions: ["Eczema (mild)", "Egg allergy", "Asthma - mild persistent", "Allergic rhinitis"], meds: ["EpiPen", "Flovent", "Albuterol PRN", "Cetirizine"], event: "Rhinitis onset, stepped up asthma", isKeyMoment: true },
    { ageMonths: 96, label: "8y", conditions: ["Egg allergy (tolerating baked)", "Asthma - mild persistent", "Allergic rhinitis"], meds: ["EpiPen", "Flovent", "Albuterol PRN", "Cetirizine"], event: "Egg challenge partial pass" },
    { ageMonths: 120, label: "10y", conditions: ["Asthma - mild persistent", "Allergic rhinitis"], meds: ["Flovent", "Albuterol PRN", "Cetirizine"], event: "Current", isKeyMoment: false },
  ];
  
  const getCurrentSnapshot = () => {
    return snapshots.reduce((prev, curr) => 
      curr.ageMonths <= currentAge ? curr : prev
    , snapshots[0]);
  };
  
  const getPreviousSnapshot = () => {
    const current = getCurrentSnapshot();
    const idx = snapshots.findIndex(s => s.ageMonths === current.ageMonths);
    return idx > 0 ? snapshots[idx - 1] : null;
  };
  
  const snapshot = getCurrentSnapshot();
  const prevSnapshot = getPreviousSnapshot();
  
  const getChanges = () => {
    if (!prevSnapshot) return { newConditions: snapshot.conditions, resolvedConditions: [], newMeds: snapshot.meds, stoppedMeds: [] };
    return {
      newConditions: snapshot.conditions.filter(c => !prevSnapshot.conditions.some(pc => pc.split(' ')[0] === c.split(' ')[0])),
      resolvedConditions: prevSnapshot.conditions.filter(c => !snapshot.conditions.some(sc => sc.split(' ')[0] === c.split(' ')[0])),
      newMeds: snapshot.meds.filter(m => !prevSnapshot.meds.includes(m)),
      stoppedMeds: prevSnapshot.meds.filter(m => !snapshot.meds.includes(m))
    };
  };
  
  const changes = getChanges();
  const ageYears = Math.floor(currentAge / 12);
  const ageMonthsRemainder = currentAge % 12;
  const ageDisplay = ageMonthsRemainder > 0 ? `${ageYears}y ${ageMonthsRemainder}mo` : `${ageYears} years`;

  return (
    <div className="min-h-screen bg-stone-100">
      {/* Header */}
      <div className="bg-gradient-to-b from-teal-800 to-teal-700 text-white px-6 py-4">
        <div className="flex justify-between items-start mb-4">
          <button className="bg-teal-900/50 hover:bg-teal-900/70 px-4 py-2 rounded-full text-sm transition-colors">
            ← Back to list
          </button>
          <button className="bg-red-800/60 hover:bg-red-800/80 px-4 py-2 rounded-full text-sm transition-colors">
            Delete
          </button>
        </div>
        
        <h1 className="text-3xl font-light mb-2">{patient.name}</h1>
        <div className="flex items-center gap-6 text-teal-100 text-sm mb-3">
          <span>{currentAge === 120 ? '10 years old' : `Viewing at ${ageDisplay}`}</span>
          <span>{patient.sex}</span>
          <span>DOB: {patient.dob}</span>
        </div>
        <span className="inline-block bg-amber-600 text-white text-xs px-3 py-1 rounded-full">
          {snapshot.conditions.length} Conditions
        </span>
        
        {/* Time Travel Toggle */}
        {showTimeline && (
          <div className="mt-6 bg-teal-900/30 rounded-xl p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-3">
                <span className="text-xs uppercase tracking-wider text-teal-200">Time Travel</span>
                <span className="text-xs bg-teal-600 px-2 py-0.5 rounded">Atopic March</span>
              </div>
              <button 
                onClick={() => setCurrentAge(120)}
                className="text-xs text-teal-200 hover:text-white transition-colors"
              >
                Reset to current
              </button>
            </div>
            
            {/* Timeline Slider */}
            <div className="relative">
              <input
                type="range"
                min="0"
                max="120"
                value={currentAge}
                onChange={(e) => setCurrentAge(parseInt(e.target.value))}
                className="w-full h-2 bg-teal-950/50 rounded-lg appearance-none cursor-pointer accent-amber-500"
              />
              
              {/* Key moment markers */}
              <div className="absolute top-0 left-0 right-0 h-2 pointer-events-none">
                {snapshots.filter(s => s.isKeyMoment).map((s, i) => (
                  <div
                    key={i}
                    className="absolute w-2 h-2 bg-amber-400 rounded-full transform -translate-x-1/2"
                    style={{ left: `${(s.ageMonths / 120) * 100}%` }}
                    title={s.event}
                  />
                ))}
              </div>
              
              {/* Age labels */}
              <div className="flex justify-between mt-2 text-xs text-teal-300">
                <span>Birth</span>
                <span>2y</span>
                <span>4y</span>
                <span>6y</span>
                <span>8y</span>
                <span>10y</span>
              </div>
            </div>
            
            {/* Current viewing indicator */}
            {currentAge !== 120 && (
              <div className="mt-3 text-center">
                <span className="text-amber-300 font-medium">
                  Viewing at {ageDisplay}
                </span>
                {snapshot.event && (
                  <span className="text-teal-200 ml-2">— {snapshot.event}</span>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      
      {/* Tabs */}
      <div className="bg-white border-b border-stone-200">
        <div className="flex gap-8 px-6">
          {['Overview', 'Encounters', 'Messages', 'Full Record', 'Timeline'].map((tab) => (
            <button
              key={tab}
              onClick={() => setViewMode(tab.toLowerCase())}
              className={`py-4 text-sm border-b-2 transition-colors ${
                viewMode === tab.toLowerCase()
                  ? 'border-teal-700 text-teal-800 font-medium'
                  : 'border-transparent text-stone-500 hover:text-stone-700'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>
      
      {/* Main Content */}
      <div className="p-6">
        {viewMode === 'overview' && (
          <>
            {/* What Changed Panel - only shows when time traveling */}
            {currentAge !== 120 && (changes.newConditions.length > 0 || changes.resolvedConditions.length > 0 || changes.newMeds.length > 0 || changes.stoppedMeds.length > 0) && (
              <div className="mb-6 bg-amber-50 border border-amber-200 rounded-xl p-4">
                <h3 className="text-sm font-medium text-amber-800 mb-3">What Changed at {ageDisplay}</h3>
                <div className="grid grid-cols-2 gap-4 text-sm">
                  {changes.newConditions.length > 0 && (
                    <div>
                      <span className="text-green-700 font-medium">+ New:</span>
                      <span className="text-stone-700 ml-2">{changes.newConditions.join(', ')}</span>
                    </div>
                  )}
                  {changes.resolvedConditions.length > 0 && (
                    <div>
                      <span className="text-stone-500 font-medium">− Resolved:</span>
                      <span className="text-stone-700 ml-2">{changes.resolvedConditions.join(', ')}</span>
                    </div>
                  )}
                  {changes.newMeds.length > 0 && (
                    <div>
                      <span className="text-blue-700 font-medium">+ Started:</span>
                      <span className="text-stone-700 ml-2">{changes.newMeds.join(', ')}</span>
                    </div>
                  )}
                  {changes.stoppedMeds.length > 0 && (
                    <div>
                      <span className="text-stone-500 font-medium">− Stopped:</span>
                      <span className="text-stone-700 ml-2">{changes.stoppedMeds.join(', ')}</span>
                    </div>
                  )}
                </div>
              </div>
            )}
            
            <h2 className="text-lg font-semibold text-stone-800 mb-4">
              Summary {currentAge !== 120 && <span className="font-normal text-stone-500">at {ageDisplay}</span>}
            </h2>
            
            {/* Stats Grid */}
            <div className="grid grid-cols-3 gap-4 mb-6">
              <div className="bg-stone-50 rounded-xl p-4">
                <div className="text-xs uppercase tracking-wider text-stone-500 mb-1">Active Conditions</div>
                <div className="text-3xl font-light text-stone-800">{snapshot.conditions.length}</div>
              </div>
              <div className="bg-stone-50 rounded-xl p-4">
                <div className="text-xs uppercase tracking-wider text-stone-500 mb-1">Active Medications</div>
                <div className="text-3xl font-light text-stone-800">{snapshot.meds.length}</div>
              </div>
              <div className="bg-stone-50 rounded-xl p-4">
                <div className="text-xs uppercase tracking-wider text-stone-500 mb-1">Encounters to Date</div>
                <div className="text-3xl font-light text-stone-800">{Math.round(currentAge * 0.625)}</div>
              </div>
            </div>
            
            {/* Conditions List */}
            <div className="bg-white rounded-xl border border-stone-200 p-4 mb-4">
              <h3 className="text-sm font-medium text-stone-700 mb-3">Active Conditions</h3>
              {snapshot.conditions.length > 0 ? (
                <div className="space-y-2">
                  {snapshot.conditions.map((condition, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-amber-500"></div>
                      <span className="text-stone-700">{condition}</span>
                      {changes.newConditions.includes(condition) && (
                        <span className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded">NEW</span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-stone-500 text-sm">No conditions at this age</p>
              )}
            </div>
            
            {/* Medications List */}
            <div className="bg-white rounded-xl border border-stone-200 p-4">
              <h3 className="text-sm font-medium text-stone-700 mb-3">Medications</h3>
              {snapshot.meds.length > 0 ? (
                <div className="space-y-2">
                  {snapshot.meds.map((med, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <div className="w-2 h-2 rounded-full bg-blue-500"></div>
                      <span className="text-stone-700">{med}</span>
                      {changes.newMeds.includes(med) && (
                        <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded">STARTED</span>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-stone-500 text-sm">No medications at this age</p>
              )}
            </div>
          </>
        )}
        
        {viewMode === 'timeline' && (
          <div>
            <h2 className="text-lg font-semibold text-stone-800 mb-4">Disease Arc: Atopic March</h2>
            <p className="text-stone-600 mb-6">
              Classic allergic progression from eczema through food allergy, asthma, and allergic rhinitis.
            </p>
            
            {/* Visual Arc */}
            <div className="relative bg-white rounded-xl border border-stone-200 p-6 mb-6">
              <div className="flex justify-between items-start">
                {[
                  { label: 'Eczema', age: '4mo', status: 'resolved', color: 'stone' },
                  { label: 'Food Allergy', age: '12mo', status: 'improving', color: 'amber' },
                  { label: 'Asthma', age: '4y', status: 'active', color: 'teal' },
                  { label: 'Allergic Rhinitis', age: '6y', status: 'active', color: 'teal' },
                ].map((stage, i) => (
                  <div key={i} className="flex flex-col items-center text-center w-1/4">
                    <div className={`w-12 h-12 rounded-full flex items-center justify-center mb-2 ${
                      stage.status === 'active' ? 'bg-teal-100 border-2 border-teal-500' :
                      stage.status === 'improving' ? 'bg-amber-100 border-2 border-amber-400' :
                      'bg-stone-100 border-2 border-stone-300'
                    }`}>
                      <span className="text-lg">{i + 1}</span>
                    </div>
                    <div className="font-medium text-stone-800">{stage.label}</div>
                    <div className="text-xs text-stone-500">Onset: {stage.age}</div>
                    <div className={`text-xs mt-1 px-2 py-0.5 rounded ${
                      stage.status === 'active' ? 'bg-teal-100 text-teal-700' :
                      stage.status === 'improving' ? 'bg-amber-100 text-amber-700' :
                      'bg-stone-100 text-stone-600'
                    }`}>
                      {stage.status}
                    </div>
                  </div>
                ))}
              </div>
              {/* Connecting line */}
              <div className="absolute top-12 left-1/8 right-1/8 h-0.5 bg-stone-200 -z-10" style={{ left: '12.5%', right: '12.5%' }}></div>
            </div>
            
            {/* Key Decision Points */}
            <h3 className="text-md font-medium text-stone-700 mb-3">Key Decision Points</h3>
            <div className="space-y-3">
              {[
                { age: '4y', question: 'Start daily controller for asthma?', decision: 'Started Flovent at age 6 after pattern of persistent symptoms' },
                { age: '8y', question: 'Attempt oral food challenge for egg?', decision: 'Partial pass - tolerates baked egg, still reactive to undercooked' },
              ].map((dp, i) => (
                <div key={i} className="bg-teal-50 border border-teal-200 rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs bg-teal-700 text-white px-2 py-0.5 rounded">Age {dp.age}</span>
                    <span className="font-medium text-teal-900">{dp.question}</span>
                  </div>
                  <p className="text-sm text-teal-800">{dp.decision}</p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default TimeTravelMockup;
