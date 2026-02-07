function _setText(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = (value == null || value === '') ? '—' : String(value);
}

const _charts = {};

function _setupChartDefaults() {
  if (typeof Chart === 'undefined' || !Chart.defaults || !Chart.defaults.global) return;

  const dpr = Math.max(1, (window.devicePixelRatio || 1));
  Chart.defaults.global.devicePixelRatio = dpr;
  Chart.defaults.global.defaultFontFamily = 'Arial, sans-serif';
  Chart.defaults.global.defaultFontColor = '#2c3e50';
  Chart.defaults.global.defaultFontSize = 12;

  Chart.defaults.global.responsive = true;
  Chart.defaults.global.maintainAspectRatio = false;
  Chart.defaults.global.animation.duration = 250;

  Chart.defaults.global.legend.labels.boxWidth = 12;
  Chart.defaults.global.legend.labels.padding = 14;

  Chart.defaults.global.tooltips.mode = 'index';
  Chart.defaults.global.tooltips.intersect = false;
  Chart.defaults.global.tooltips.backgroundColor = 'rgba(44,62,80,0.92)';
  Chart.defaults.global.tooltips.titleFontSize = 13;
  Chart.defaults.global.tooltips.bodyFontSize = 12;
  Chart.defaults.global.tooltips.xPadding = 10;
  Chart.defaults.global.tooltips.yPadding = 10;

  if (Chart.defaults.global.elements && Chart.defaults.global.elements.line) {
    Chart.defaults.global.elements.line.borderWidth = 2;
    Chart.defaults.global.elements.line.tension = 0.25;
  }
  if (Chart.defaults.global.elements && Chart.defaults.global.elements.point) {
    Chart.defaults.global.elements.point.radius = 2;
    Chart.defaults.global.elements.point.hoverRadius = 4;
    Chart.defaults.global.elements.point.hitRadius = 8;
  }
}

function _renderChart(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas || typeof Chart === 'undefined') return;
  if (_charts[id]) {
    try { _charts[id].destroy(); } catch (e) {}
    delete _charts[id];
  }
  const ctx = canvas.getContext('2d');
  _charts[id] = new Chart(ctx, config);
}

function _formatMinutesToMinSec(mins) {
  if (mins == null || Number.isNaN(Number(mins))) return '—';
  const totalSeconds = Math.max(0, Math.round(Number(mins) * 60));
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

async function _fetchJson(url) {
  const res = await fetch(url, { method: 'GET', credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return await res.json();
}

async function loadDashboard() {
  let kpis = null;
  let charts = null;

  try {
    kpis = await _fetchJson('api/kpis/');
  } catch (e) {
    kpis = null;
  }

  if (kpis) {
    _setText('kpi-active-users', kpis.active_users);
    _setText('kpi-rides-today', kpis.rides_today);
    _setText('kpi-cancellations', kpis.cancellations);
    _setText('kpi-wait-time', _formatMinutesToMinSec(kpis.avg_wait_minutes));
    _setText('kpi-completed-trips', kpis.completed_trips);
    _setText('kpi-flagged', kpis.flagged_incidents);
  }

  try {
    charts = await _fetchJson('api/chart-data/');
  } catch (e) {
    charts = null;
  }

  if (!charts) return;

  _setupChartDefaults();

  const labels = Array.isArray(charts.labels) ? charts.labels : ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const tsRides = Array.isArray(charts.tsRides) ? charts.tsRides : [];
  const byHourLabels = Array.isArray(charts.byHourLabels) ? charts.byHourLabels : ['0h','4h','8h','12h','16h','20h','24h'];
  const byHour = Array.isArray(charts.byHour) ? charts.byHour : [];
  const drivers = Array.isArray(charts.drivers) ? charts.drivers : [];
  const riders = Array.isArray(charts.riders) ? charts.riders : [];
  const cancelReasons = Array.isArray(charts.cancelReasons) ? charts.cancelReasons : [];
  const completedTrips = Array.isArray(charts.completedTrips) ? charts.completedTrips : tsRides;
  const avgWait = Array.isArray(charts.avgWait) ? charts.avgWait : [];

  const gridColor = 'rgba(44, 62, 80, 0.12)';
  const axisLabelColor = '#2c3e50';
  const tickColor = '#34495e';
  const baseLayout = { padding: { top: 6, right: 10, bottom: 6, left: 8 } };

  function _lineDatasetDefaults() {
    return {
      borderWidth: 2,
      pointRadius: 2,
      pointHoverRadius: 4,
      pointHitRadius: 8,
      lineTension: 0.25
    };
  }

  // 1) Time‑series: rides/day over past week
  _renderChart('tsRidesChart', {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Rides Completed',
        data: tsRides,
        backgroundColor: 'rgba(46,204,113,0.2)',
        borderColor: '#27ae60',
        fill: true,
        ..._lineDatasetDefaults()
      }]
    },
    options: {
      title: { display:true, text:'Daily Completed Rides' },
      responsive:true,
      maintainAspectRatio:false,
      layout: baseLayout,
      scales: {
        xAxes: [{
          gridLines: { display: false },
          ticks: { fontColor: tickColor, maxRotation: 0, autoSkip: true },
        }],
        yAxes: [{
          gridLines: { color: gridColor, drawBorder: false },
          ticks: { beginAtZero: true, fontColor: tickColor, precision: 0 },
          scaleLabel: { display: true, labelString: 'Trips', fontColor: axisLabelColor }
        }]
      }
    }
  });

  // 2) Heatmap‑style: request density by hour (using a bar‑gradient hack)
  const byHourColors = byHour.map(v => (v > 300 ? '#c0392b' : (v > 200 ? '#e67e22' : '#f1c40f')));
  _renderChart('ridesByHourHeatmap', {
    type: 'bar',
    data: {
      labels: byHourLabels,
      datasets:[{
        label:'Bookings (last 24h)',
        data: byHour,
        backgroundColor: byHourColors,
        borderColor: byHourColors,
        borderWidth: 1,
        barPercentage: 0.75,
        categoryPercentage: 0.8
      }]
    },
    options: {
      title:{ display:true, text:'Booking Density (last 24h)' },
      legend:{ display:false },
      responsive:true,
      maintainAspectRatio:false,
      layout: baseLayout,
      scales: {
        xAxes: [{
          gridLines: { display: false },
          ticks: { fontColor: tickColor }
        }],
        yAxes: [{
          gridLines: { color: gridColor, drawBorder: false },
          ticks: { beginAtZero: true, fontColor: tickColor, precision: 0 },
          scaleLabel: { display: true, labelString: 'Bookings', fontColor: axisLabelColor }
        }]
      }
    }
  });

  // 3) User Growth: active users by day
  _renderChart('userGrowthChart', {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Active Drivers',
          data: drivers,
          backgroundColor: 'rgba(52,152,219,0.2)',
          borderColor: '#2980b9',
          fill: true,
          ..._lineDatasetDefaults()
        },
        {
          label: 'Active Riders',
          data: riders,
          backgroundColor: 'rgba(155,89,182,0.2)',
          borderColor: '#9b59b6',
          fill: true,
          ..._lineDatasetDefaults()
        }
      ]
    },
    options: {
      title:{ display:true, text:'Daily Active Users' },
      responsive:true,
      maintainAspectRatio:false,
      layout: baseLayout,
      scales: {
        xAxes: [{
          gridLines: { display: false },
          ticks: { fontColor: tickColor, maxRotation: 0, autoSkip: true },
        }],
        yAxes: [{
          gridLines: { color: gridColor, drawBorder: false },
          ticks: { beginAtZero: true, fontColor: tickColor, precision: 0 },
          scaleLabel: { display: true, labelString: 'Users', fontColor: axisLabelColor }
        }]
      }
    }
  });

  // 4) Cancellation Reasons: doughnut
  _renderChart('cancellationDoughnut', {
    type:'doughnut',
    data:{
      labels:['Booking Cancelled','Trip Cancelled','Safety (Trip reason contains "safety")','Other Trip Cancellation'],
      datasets:[{
        data: cancelReasons,
        backgroundColor:['#e74c3c','#f39c12','#c0392b','#7f8c8d'],
        borderColor: '#ffffff',
        borderWidth: 2
      }]
    },
    options:{
      title:{ display:true, text:'Cancellation Breakdown (last 7 days)' },
      responsive:true,
      maintainAspectRatio:false,
      layout: baseLayout,
      cutoutPercentage: 65,
      legend: {
        position: 'right',
        labels: { fontColor: tickColor, usePointStyle: true }
      }
    }
  });

  // 5) Wait vs. Completed: dual‑axis bar+line
  _renderChart('waitVsCompleted', {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          type:'bar',
          label:'Completed Trips',
          data: completedTrips,
          backgroundColor:'#2ecc71',
          yAxisID:'y1',
          borderColor:'#27ae60',
          borderWidth: 1,
          barPercentage: 0.75,
          categoryPercentage: 0.8
        },
        {
          type:'line',
          label:'Avg Wait (min)',
          data: avgWait,
          borderColor:'#e67e22',
          fill:false,
          yAxisID:'y2',
          spanGaps: true,
          ..._lineDatasetDefaults()
        }
      ]
    },
    options:{
      title:{ display:true, text:'Trips vs. Avg. Wait Time' },
      responsive:true,
      maintainAspectRatio:false,
      layout: baseLayout,
      scales:{
        yAxes:[
          { id:'y1', position:'left', ticks:{ beginAtZero:true, fontColor: tickColor, precision: 0 }, scaleLabel:{ display:true, labelString:'Trips', fontColor: axisLabelColor }, gridLines: { color: gridColor, drawBorder: false } },
          { id:'y2', position:'right', ticks:{ beginAtZero:true, fontColor: tickColor }, scaleLabel:{ display:true, labelString:'Wait (min)', fontColor: axisLabelColor }, gridLines:{ drawOnChartArea:false } }
        ],
        xAxes: [{
          gridLines: { display: false },
          ticks: { fontColor: tickColor, maxRotation: 0, autoSkip: true }
        }]
      }
    }
  });
}

loadDashboard();
