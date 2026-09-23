const state = {
    jobs: [],
    category: 'all',
    query: '',
    sortBy: null,
    descending: true,
    selected: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
}

function safePath(job, file) {
    return job.dir_path.split('/').map(encodeURIComponent).join('/') + '/' + encodeURIComponent(file);
}

function score(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

async function loadJobs() {
    const response = await fetch('/api/jobs', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const jobs = await response.json();
    if (!Array.isArray(jobs)) throw new Error('Invalid job list');
    state.jobs = jobs;
    $('#loading-state').classList.add('hidden');
    updateCounts();
    renderJobs();
}

function updateCounts() {
    $('#count-all').textContent = state.jobs.length;
    $('#count-apply').textContent = state.jobs.filter(job => job.category === 'apply').length;
    $('#count-review').textContent = state.jobs.filter(job => job.category === 'need-review').length;
    $('#count-skip').textContent = state.jobs.filter(job => job.category === 'skip').length;
}

function renderJobs() {
    const query = state.query.toLocaleLowerCase();
    const jobs = state.jobs.filter(job =>
        (state.category === 'all' || job.category === state.category) &&
        (`${job.title} ${job.company}`).toLocaleLowerCase().includes(query)
    );
    if (state.sortBy) {
        jobs.sort((a, b) => {
            const first = score(a[state.sortBy]);
            const second = score(b[state.sortBy]);
            if (first === null) return second === null ? 0 : 1;
            if (second === null) return -1;
            return state.descending ? second - first : first - second;
        });
    }

    const body = $('#jobs-tbody');
    body.replaceChildren();
    $('#empty-state').classList.toggle('hidden', jobs.length > 0);
    $('.table-container').classList.toggle('hidden', jobs.length === 0);

    for (const job of jobs) {
        const row = element('tr', 'job-row');
        row.dataset.category = job.category;

        const titleCell = element('td', 'td-title');
        const title = element(job.source_url ? 'a' : 'span', 'source-link', job.title);
        if (job.source_url) {
            title.href = job.source_url;
            title.target = '_blank';
            title.rel = 'noopener noreferrer';
        }
        titleCell.append(title);
        const badge = element('span', `badge badge-${job.category === 'need-review' ? 'review' : job.category} ml-2`, job.category);
        titleCell.append(badge);
        row.append(titleCell);
        row.append(element('td', 'td-company', job.company));

        for (const key of ['avg_match', 'red_team_match']) {
            const cell = element('td', 'td-score');
            cell.append(element('span', 'score-badge', score(job[key]) ?? '–'));
            row.append(cell);
        }

        const actions = element('td', 'td-actions');
        if (job.files?.resume_pdf) {
            const button = element('button', 'action-btn', '📄 Resume');
            button.type = 'button';
            button.addEventListener('click', () => window.open(`/api/preview/${safePath(job, 'resume.pdf')}`, '_blank', 'noopener'));
            actions.append(button);
        }
        if (job.files?.cover_letter) {
            const button = element('button', 'action-btn', '✉️ Cover Letter');
            button.type = 'button';
            button.addEventListener('click', () => openJob(job, 'cover-letter'));
            actions.append(button);
        }
        const details = element('button', 'action-btn primary', '🔍 Details');
        details.type = 'button';
        details.addEventListener('click', () => openJob(job, 'vacancy'));
        actions.append(details);
        row.append(actions);
        body.append(row);
    }
}

function openJob(job, tab) {
    state.selected = job;
    $('#modal-job-title').textContent = job.title;
    $('#modal-company-name').textContent = job.company;
    $$('.tab-content').forEach(content => content.replaceChildren());
    $('#job-modal').classList.remove('hidden');
    switchTab(tab);
}

function closeJob() {
    $('#job-modal').classList.add('hidden');
    state.selected = null;
}

function switchTab(tab) {
    $$('.tab-btn').forEach(button => button.classList.toggle('active', button.dataset.tab === tab));
    $$('.tab-content').forEach(content => content.classList.toggle('active', content.id === `tab-${tab}`));
    if (state.selected) loadTab(state.selected, tab);
}

async function fetchText(job, file) {
    const response = await fetch(`/api/job/${safePath(job, file)}`, { cache: 'no-store' });
    if (!response.ok) return null;
    return response.text();
}

function showText(container, text, className = 'json-view') {
    container.replaceChildren(element('pre', className, text));
}

async function loadTab(job, tab) {
    const container = $(`#tab-${tab}`);
    showText(container, 'Loading…');
    try {
        if (tab === 'vacancy') {
            const markdown = await fetchText(job, 'vacancy-source.md');
            const json = markdown === null ? await fetchText(job, 'vacancy.json') : null;
            showText(container, markdown ?? json ?? 'No vacancy details found.');
        } else if (tab === 'evaluations') {
            const reports = await Promise.all([
                fetchText(job, 'avg-match.json'),
                fetchText(job, 'red-team-match.json'),
            ]);
            showText(container, reports.map((value, index) =>
                value === null ? '' : `${index === 0 ? 'Average match' : 'Red team match'}\n${value}`
            ).filter(Boolean).join('\n\n') || 'No evaluations found.');
        } else if (tab === 'resume') {
            if (job.files?.resume_pdf || job.files?.resume_png) {
                const isPdf = job.files.resume_pdf;
                const file = isPdf ? 'resume.pdf' : 'resume-preview.png';
                const wrapper = element('div', isPdf ? 'pdf-container' : 'img-container');
                const preview = element(isPdf ? 'iframe' : 'img');
                preview.src = `/api/preview/${safePath(job, file)}`;
                preview.title = 'Resume preview';
                wrapper.append(preview);
                container.replaceChildren(wrapper);
            } else {
                showText(container, await fetchText(job, 'resume.md') ?? 'No resume generated.');
            }
        } else if (tab === 'cover-letter') {
            showText(container, await fetchText(job, 'cover-letter.md') ?? 'No cover letter generated.');
        }
    } catch (error) {
        showText(container, `Could not load content: ${error.message}`);
    }
}

function bindEvents() {
    $$('.nav-item').forEach(button => button.addEventListener('click', () => {
        $$('.nav-item').forEach(item => item.classList.remove('active'));
        button.classList.add('active');
        state.category = button.dataset.category;
        $('#current-view-title').textContent = button.querySelector('span:nth-child(2)').textContent;
        renderJobs();
    }));
    $('#search-input').addEventListener('input', event => {
        state.query = event.target.value;
        renderJobs();
    });
    $('#close-modal').addEventListener('click', closeJob);
    $('#job-modal').addEventListener('click', event => {
        if (event.target.id === 'job-modal') closeJob();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeJob();
    });
    $$('.tab-btn').forEach(button => button.addEventListener('click', () => switchTab(button.dataset.tab)));
    $$('.sortable').forEach(header => header.addEventListener('click', () => {
        const key = header.dataset.sort === 'avg' ? 'avg_match' : 'red_team_match';
        state.descending = state.sortBy === key ? !state.descending : true;
        state.sortBy = key;
        $$('.sortable').forEach(item => {
            item.classList.remove('sorted-asc', 'sorted-desc');
            if (item === header) item.classList.add(state.descending ? 'sorted-desc' : 'sorted-asc');
        });
        renderJobs();
    }));
}

document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    loadJobs().catch(error => {
        $('#loading-state').replaceChildren(element('p', null, `Could not load results: ${error.message}`));
    });
});
