/** Shared page element references and caches read by multiple areas. */
const filesEl = document.getElementById('files');
const toniesContentEl = document.getElementById('tonies-content');
const toniesStatusEl = document.getElementById('tonies-status');
const searchLimitLabelEl = document.getElementById('search-limit-label');

let filesCache = [];
let jobsCache = [];
let toniesCache = [];
let selectedToniesUrl = '';
let toniesImageCache = {};
let toniesContentCache = { chapters: [], free_minutes: null, total_minutes: 90, used_minutes: null };
let toniesContentByUrlCache = {};
