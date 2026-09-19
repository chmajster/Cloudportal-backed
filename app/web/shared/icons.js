'use strict';

(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const DEFINITIONS = {
    dashboard: [['rect',{x:3,y:3,width:7,height:7,rx:2}],['rect',{x:14,y:3,width:7,height:7,rx:2}],['rect',{x:3,y:14,width:7,height:7,rx:2}],['rect',{x:14,y:14,width:7,height:7,rx:2}]],
    users: [['path',{d:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2'}],['circle',{cx:9,cy:7,r:4}],['path',{d:'M22 21v-2a4 4 0 0 0-3-3.87'}],['path',{d:'M16 3.13a4 4 0 0 1 0 7.75'}]],
    shield: [['path',{d:'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10'}],['path',{d:'m9 12 2 2 4-4'}]],
    key: [['circle',{cx:7.5,cy:15.5,r:5.5}],['path',{d:'m12 12 8-8'}],['path',{d:'m16 8 2 2'}],['path',{d:'m18 6 2 2'}]],
    database: [['ellipse',{cx:12,cy:5,rx:8,ry:3}],['path',{d:'M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5'}],['path',{d:'M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6'}]],
    server: [['rect',{x:3,y:4,width:18,height:6,rx:2}],['rect',{x:3,y:14,width:18,height:6,rx:2}],['path',{d:'M7 7h.01'}],['path',{d:'M7 17h.01'}],['path',{d:'M17 7h1'}],['path',{d:'M17 17h1'}]],
    box: [['path',{d:'m21 8-9 5-9-5 9-5 9 5Z'}],['path',{d:'m3 8 9 5 9-5'}],['path',{d:'M3 8v8l9 5 9-5V8'}],['path',{d:'M12 13v8'}]],
    workflow: [['rect',{x:3,y:3,width:6,height:6,rx:1.5}],['rect',{x:15,y:15,width:6,height:6,rx:1.5}],['path',{d:'M9 6h3a3 3 0 0 1 3 3v6'}],['path',{d:'m12 12 3 3 3-3'}]],
    network: [['rect',{x:8,y:2,width:8,height:6,rx:2}],['rect',{x:2,y:16,width:8,height:6,rx:2}],['rect',{x:14,y:16,width:8,height:6,rx:2}],['path',{d:'M12 8v4'}],['path',{d:'M6 16v-2h12v2'}]],
    globe: [['circle',{cx:12,cy:12,r:9}],['path',{d:'M3 12h18'}],['path',{d:'M12 3a14 14 0 0 1 0 18'}],['path',{d:'M12 3a14 14 0 0 0 0 18'}]],
    monitor: [['rect',{x:2,y:3,width:20,height:14,rx:2}],['path',{d:'M8 21h8'}],['path',{d:'M12 17v4'}]],
    rocket: [['path',{d:'M4.5 16.5c-1.5 1-2.5 3-2.5 5 2 0 4-1 5-2.5'}],['path',{d:'M9 15 4 10l3-3 5 1'}],['path',{d:'m15 9 1 5 3 3 3-3-1-5'}],['path',{d:'M9 15c4-1 8-5 10-11-6 2-10 6-11 10'}],['circle',{cx:15,cy:8,r:1.5}]],
    'list-check': [['path',{d:'m3 6 1.5 1.5L7 5'}],['path',{d:'M10 6h11'}],['path',{d:'m3 12 1.5 1.5L7 11'}],['path',{d:'M10 12h11'}],['path',{d:'m3 18 1.5 1.5L7 17'}],['path',{d:'M10 18h11'}]],
    calendar: [['rect',{x:3,y:5,width:18,height:16,rx:2}],['path',{d:'M16 3v4'}],['path',{d:'M8 3v4'}],['path',{d:'M3 10h18'}]],
    webhook: [['path',{d:'M18 16.5a3.5 3.5 0 1 1-3.45 4.1'}],['path',{d:'m18 13-3 5h6'}],['path',{d:'M6 7.5A3.5 3.5 0 1 1 9.45 3.4'}],['path',{d:'m6 11 3-5H3'}],['path',{d:'M10.2 18H6.5a3.5 3.5 0 1 1 3.03-5.25'}]],
    activity: [['path',{d:'M3 12h4l2-7 4 14 2-7h6'}]],
    wrench: [['path',{d:'M14.7 6.3a4 4 0 0 0-5-5L7 4l3 3 2.7-2.7a4 4 0 0 0 2 5L7.5 16.5a3 3 0 1 0 4.2 4.2l7.2-7.2a4 4 0 0 0 5-5l-2.7 2.7-3-3 2.7-2.7a4 4 0 0 0-6.2.8Z'}]],
    'file-text': [['path',{d:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'}],['path',{d:'M14 2v6h6'}],['path',{d:'M8 13h8'}],['path',{d:'M8 17h8'}],['path',{d:'M8 9h2'}]],
    user: [['circle',{cx:12,cy:8,r:4}],['path',{d:'M4 21a8 8 0 0 1 16 0'}]],
    search: [['circle',{cx:11,cy:11,r:7}],['path',{d:'m20 20-4-4'}]],
    refresh: [['path',{d:'M20 6v5h-5'}],['path',{d:'M4 18v-5h5'}],['path',{d:'M18.5 9A7 7 0 0 0 6 6.5L4 11'}],['path',{d:'M5.5 15A7 7 0 0 0 18 17.5L20 13'}]],
    sun: [['circle',{cx:12,cy:12,r:4}],['path',{d:'M12 2v2'}],['path',{d:'M12 20v2'}],['path',{d:'m4.93 4.93 1.41 1.41'}],['path',{d:'m17.66 17.66 1.41 1.41'}],['path',{d:'M2 12h2'}],['path',{d:'M20 12h2'}],['path',{d:'m6.34 17.66-1.41 1.41'}],['path',{d:'m19.07 4.93-1.41 1.41'}]],
    moon: [['path',{d:'M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79'}]],
    menu: [['path',{d:'M4 6h16'}],['path',{d:'M4 12h16'}],['path',{d:'M4 18h16'}]],
    'log-out': [['path',{d:'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'}],['path',{d:'m16 17 5-5-5-5'}],['path',{d:'M21 12H9'}]],
    'chevron-right': [['path',{d:'m9 18 6-6-6-6'}]],
    'arrow-left': [['path',{d:'m15 18-6-6 6-6'}],['path',{d:'M9 12h10'}]],
    check: [['path',{d:'m5 12 4 4L19 6'}]],
    cpu: [['rect',{x:7,y:7,width:10,height:10,rx:2}],['path',{d:'M9 1v3'}],['path',{d:'M15 1v3'}],['path',{d:'M9 20v3'}],['path',{d:'M15 20v3'}],['path',{d:'M20 9h3'}],['path',{d:'M20 14h3'}],['path',{d:'M1 9h3'}],['path',{d:'M1 14h3'}]]
  };

  const ROUTE_ICONS = {
    dashboard: 'dashboard',
    users: 'users',
    roles: 'shield',
    tokens: 'key',
    credentials: 'database',
    providers: 'server',
    catalog: 'box',
    blueprints: 'workflow',
    hostnames: 'network',
    ipam: 'globe',
    inventory: 'monitor',
    deployments: 'rocket',
    jobs: 'list-check',
    schedules: 'calendar',
    webhooks: 'webhook',
    observability: 'activity',
    tools: 'wrench',
    updates: 'refresh',
    audit: 'file-text',
    account: 'user'
  };

  function appIcon(name, options = {}) {
    const resolved = DEFINITIONS[name] ? name : (ROUTE_ICONS[name] || 'box');
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', options.strokeWidth || '1.9');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', options.ariaHidden === false ? 'false' : 'true');
    if (options.className) svg.setAttribute('class', options.className);
    for (const [tag, attrs] of DEFINITIONS[resolved]) {
      const child = document.createElementNS(NS, tag);
      Object.entries(attrs).forEach(([key, value]) => child.setAttribute(key, value));
      svg.append(child);
    }
    return svg;
  }

  window.appIcon = appIcon;
  window.appRouteIcon = route => appIcon(route?.iconName || ROUTE_ICONS[route?.id] || route?.id || 'box');
})();
