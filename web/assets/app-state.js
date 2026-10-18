/** Shared page state: element references, caches, flags and storage keys. */
const filesEl = document.getElementById('files');

const localPlayerEl = document.getElementById('local-player');

const localAudioEl = document.getElementById('local-audio');

const localPlayerNameEl = document.getElementById('local-player-name');

const localPlayerStatusEl = document.getElementById('local-player-status');

let localPlaybackFilename = '';

let localPlaybackIntent = 0;

const jobsEl = document.getElementById('jobs');

const resultsLoaderEl = document.getElementById('results-loader');

const searchBtnEl = document.getElementById('search-btn');

const queryInputEl = document.getElementById('query-input');

const clearResultsEl = document.getElementById('clear-results');

const fileFilterEl = document.getElementById('file-filter');

const browseFilesEl = document.getElementById('browse-files');

const uploadFileInputEl = document.getElementById('upload-file-input');

const toniesContentEl = document.getElementById('tonies-content');

const toniesStatusEl = document.getElementById('tonies-status');

const toniesMinutesEl = document.getElementById('tonies-minutes');

const toniesProgressEl = document.getElementById('tonies-progress');

const toggleToniesOrderEl = document.getElementById('toggle-tonies-order');

const toggleToniesEditEl = document.getElementById('toggle-tonies-edit');

const toniesEditActionsEl = document.getElementById('tonies-edit-actions');

const saveToniesNamesEl = document.getElementById('save-tonies-names');

const cancelToniesNamesEl = document.getElementById('cancel-tonies-names');

const deleteAllToniesContentEl = document.getElementById('delete-all-tonies-content');

const confirmModalEl = document.getElementById('confirm-modal');

const confirmTitleEl = document.getElementById('confirm-title');

const confirmMessageEl = document.getElementById('confirm-message');

const confirmOkEl = document.getElementById('confirm-ok');

const confirmCancelEl = document.getElementById('confirm-cancel');

const toastStackEl = document.getElementById('toast-stack');

const toniesMenuEl = document.getElementById('tonies-menu');

const toniesMenuTopEl = document.getElementById('tonies-menu-top');

const toniesMenuBottomEl = document.getElementById('tonies-menu-bottom');

const toniesMenuUpEl = document.getElementById('tonies-menu-up');

const toniesMenuDownEl = document.getElementById('tonies-menu-down');

const toniesMenuCancelEl = document.getElementById('tonies-menu-cancel');

const fileMenuEl = document.getElementById('file-menu');

const fileMenuUploadEl = document.getElementById('file-menu-upload');

const fileMenuUploadTopEl = document.getElementById('file-menu-upload-top');

const fileMenuUploadBottomEl = document.getElementById('file-menu-upload-bottom');

const fileMenuDownloadEl = document.getElementById('file-menu-download');

const fileMenuCancelEl = document.getElementById('file-menu-cancel');

const stCount = document.getElementById('st-count');

const stSize = document.getElementById('st-size');

const stLength = document.getElementById('st-length');

const lockAppEl = document.getElementById('lock-app');

const realtimeStatusEl = document.getElementById('realtime-status');

const searchLimitLabelEl = document.getElementById('search-limit-label');

let filesCache = [];

let jobsCache = [];

let toniesCache = [];

let selectedToniesUrl = '';

const FILES_CACHE_KEY = 'tonies-auto.files-cache.v1';

const TONIES_CACHE_KEY = 'tonies-auto.creative-tonies.v1';

const TONIES_SELECTED_KEY = 'tonies-auto.selected-tonies-url.v1';

const TONIES_IMAGE_CACHE_KEY = 'tonies-auto.creative-tonies.image-cache.v1';

const TONIES_CONTENT_CACHE_KEY = 'tonies-auto.tonies-content-by-url.v1';

const TONIES_REVERSE_VIEW_KEY = 'tonies-auto.tonies-reverse-view.v1';

const RESULTS_CLEARED_KEY = 'tonies-auto.results-cleared.v1';

const SEARCH_LIMIT_KEY = 'tonies-auto.search-limit.v1';

let toniesImageCache = {};

let toniesImagesLoading = false;

let toniesContentCache = { chapters: [], free_minutes: null, total_minutes: 90, used_minutes: null };

let toniesApplying = false;

let toniesReverseView = true;

let toniesEditMode = false;

let toniesNameDrafts = {};

let toniesContentRequestSeq = 0;

const toniesContentVersionByUrl = {};

const toniesContentReadSeqByUrl = {};  // latest-started library read per Tonie

let toniesContentByUrlCache = {};

const jobStatusById = {};

let highlightedJobId = '';

let highlightJobTimer = null;

let recentUploadedChapterIds = {};

const completedUploadJobIds = new Set();

const reconciledUploadReceiptJobIds = new Set();

const toniesUploadReconciliations = [];

let toniesUploadReconciliationRunning = false;

const uploadIntentsByJobId = new Map();

let toniesProcessingTimer = null;

let toniesProcessingPolls = 0;

const localUploadToastShownJobIds = new Set();

let toniesNewClearTimer = null;

// Library edits queued on the server (app/actions.py), by action id. The list shows
// the server's last content with these still-pending edits applied on top.
const toniesQueuedActions = new Map();

const finishedToniesActionIds = new Set();

const attachedUploadJobIds = new Set();  // upload jobs whose chapter is already in the list

let toniesActionPollTimer = null;

let fileMenuFilename = '';

const TONIES_NEW_HIGHLIGHT_MS = 5000;

let loadingFiles = false;

let searchResultsCutoffMs = null;

let activeSearchJobId = '';

let resultsCleared = false;

const START_WITH_CLEARED_RESULTS = true;

let searchLimit = 5;

let searchUiActive = false;

let loadingJobs = false;

let loadingToniesContent = false;

let loadingToniesContentUrl = '';

let hasLoadedJobsOnce = false;

let toniesUploadSyncing = false;

let loadingTonies = false;

let toniesLoadError = '';

let toniesMenuChapterId = null;

let lastToniesTapIndex = null;

let lastToniesTapAt = 0;

let pendingDropUploadIntent = null;

let pendingToniesUpload = null;

let queuedLocalUploads = [];

let queuedLocalUploadNames = new Set();

let localUploadQueuePumping = false;

let localUploadOutstandingByName = {};

let activeUploadJobIds = new Set();

let selectingCandidateJobIds = new Set();

let selectedCandidateIndexByJob = {};

let syncingResultJobIds = new Set();

let toniesStatsSyncing = new Set();

let toniesStatsSyncBusy = false;

let isBackendOnline = true;

let queuedSearchMessage = '';

let queuedSearchRetryTimer = null;

let activeSearchRequestController = null;

let activeSearchRequestSeq = 0;

let lastSearchStartedAtMs = 0;

const ACTIVE_JOB_STATUSES = new Set(['queued', 'parsing', 'searching', 'awaiting_selection', 'queued_download', 'downloading', 'queued_prepare', 'preparing', 'waiting_upload', 'uploading']);

const SEARCH_READY_STATUSES = new Set(['awaiting_selection', 'failed']);

const SEARCH_PENDING_STATUSES = new Set(['queued', 'parsing', 'searching']);

const CANCELLABLE_JOB_STATUSES = new Set(['queued', 'parsing', 'searching', 'awaiting_selection', 'queued_download', 'downloading', 'queued_prepare', 'preparing', 'waiting_upload', 'uploading']);
