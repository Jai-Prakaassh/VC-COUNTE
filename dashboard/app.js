/**
 * Discord VC Counter • Real-Time Dashboard Frontend Logic
 * Implements WebSocket lifecycle with exponential backoff, real-time audio state rendering,
 * search/filter controls, multi-server switching, and administrative authentication.
 */

(function () {
    'use strict';

    // State Store
    const state = {
        guilds: [],
        currentGuildId: null,
        currentGuildStats: null,
        authToken: sessionStorage.getItem('vc_counter_auth_token') || '',
        isAdmin: false,
        searchQuery: '',
        filterMode: 'all', // 'all' | 'active_only' | 'empty_only'
        sortBy: 'occupants_desc', // 'occupants_desc' | 'occupants_asc' | 'name_asc' | 'name_desc'
        ws: null,
        wsReconnectAttempts: 0,
        wsHeartbeatTimer: null,
        fallbackPollTimer: null,
        lastSyncTimestamp: null,
        activeModalUserId: null,
        userStatsLiveTicker: null,
        modalUserData: null,
        leaderboardData: [],
        leaderboardLiveTicker: null,
        leaderboardAutoPollTimer: null,
        leaderboardTimeframe: 'all',
        currentTab: 'activityTab',
    };

    // DOM Element References
    const elements = {
        serverSelect: document.getElementById('serverSelect'),
        selectedServerIcon: document.getElementById('selectedServerIcon'),
        connectionStatus: document.getElementById('connectionStatus'),
        connectionText: document.getElementById('connectionText'),
        refreshBtn: document.getElementById('refreshBtn'),
        refreshIcon: document.getElementById('refreshIcon'),
        authModalBtn: document.getElementById('authModalBtn'),
        authBtnLabel: document.getElementById('authBtnLabel'),
        privateLockBanner: document.getElementById('privateLockBanner'),
        unlockBtn: document.getElementById('unlockBtn'),
        metricsSection: document.getElementById('metricsSection'),
        statTotalMembers: document.getElementById('statTotalMembers'),
        statHumanMembers: document.getElementById('statHumanMembers'),
        statBotMembers: document.getElementById('statBotMembers'),
        statInVoice: document.getElementById('statInVoice'),
        statInVoiceHumans: document.getElementById('statInVoiceHumans'),
        statInVoiceBots: document.getElementById('statInVoiceBots'),
        statActiveVC: document.getElementById('statActiveVC'),
        statActivePercent: document.getElementById('statActivePercent'),
        statTotalAudio: document.getElementById('statTotalAudio'),
        statTotalVoice: document.getElementById('statTotalVoice'),
        statTotalStage: document.getElementById('statTotalStage'),
        channelSearchInput: document.getElementById('channelSearchInput'),
        clearSearchBtn: document.getElementById('clearSearchBtn'),
        filterChips: document.querySelectorAll('.filter-chip'),
        activeChannelsCountPill: document.getElementById('activeChannelsCountPill'),
        sortBySelect: document.getElementById('sortBySelect'),
        lastUpdatedTimestamp: document.getElementById('lastUpdatedTimestamp'),
        channelsCountBadge: document.getElementById('channelsCountBadge'),
        channelsContainer: document.getElementById('channelsContainer'),
        noChannelsFound: document.getElementById('noChannelsFound'),
        activityFeedList: document.getElementById('activityFeedList'),
        clearFeedBtn: document.getElementById('clearFeedBtn'),
        leaderboardList: document.getElementById('leaderboardList'),
        refreshLeaderboardBtn: document.getElementById('refreshLeaderboardBtn'),
        leaderboardSearchInput: document.getElementById('leaderboardSearchInput'),
        leaderboardCountBadge: document.getElementById('leaderboardCountBadge'),
        sidebarTabBtns: document.querySelectorAll('.sidebar-tabs .tab-btn'),
        tabPanes: {
            activityTab: document.getElementById('activityTab'),
            leaderboardTab: document.getElementById('leaderboardTab'),
        },
        botLatencyValue: document.getElementById('botLatencyValue'),
        counterStatusValue: document.getElementById('counterStatusValue'),
        dashboardModeValue: document.getElementById('dashboardModeValue'),
        authModal: document.getElementById('authModal'),
        closeModalBtn: document.getElementById('closeModalBtn'),
        cancelAuthBtn: document.getElementById('cancelAuthBtn'),
        submitAuthBtn: document.getElementById('submitAuthBtn'),
        adminSecretInput: document.getElementById('adminSecretInput'),
        modalAuthFeedback: document.getElementById('modalAuthFeedback'),
        toastContainer: document.getElementById('toastContainer'),
        userStatsModal: document.getElementById('userStatsModal'),
        closeUserStatsModalBtn: document.getElementById('closeUserStatsModalBtn'),
        userStatsAvatar: document.getElementById('userStatsAvatar'),
        userStatsLiveDot: document.getElementById('userStatsLiveDot'),
        userStatsDisplayName: document.getElementById('userStatsDisplayName'),
        userStatsStatusBadge: document.getElementById('userStatsStatusBadge'),
        userStatsTotalTime: document.getElementById('userStatsTotalTime'),
        userAllTimeUnmuted: document.getElementById('userAllTimeUnmuted'),
        userAllTimeMuted: document.getElementById('userAllTimeMuted'),
        userAllTimeDeafened: document.getElementById('userAllTimeDeafened'),
        userAllTimeAfk: document.getElementById('userAllTimeAfk'),
        userDailyTotal: document.getElementById('userDailyTotal'),
        userDailyUnmuted: document.getElementById('userDailyUnmuted'),
        userDailyMuted: document.getElementById('userDailyMuted'),
        userDailyDeafened: document.getElementById('userDailyDeafened'),
        userDailyAfk: document.getElementById('userDailyAfk'),
        userWeeklyTotal: document.getElementById('userWeeklyTotal'),
        userWeeklyUnmuted: document.getElementById('userWeeklyUnmuted'),
        userWeeklyMuted: document.getElementById('userWeeklyMuted'),
        userWeeklyDeafened: document.getElementById('userWeeklyDeafened'),
        userWeeklyAfk: document.getElementById('userWeeklyAfk'),
    };

    // ==============================================================================
    // INITIALIZATION & GUILD LOADING
    // ==============================================================================
    async function init() {
        bindEvents();
        updateAuthButtonUI();
        await fetchSystemHealth();
        await loadGuilds();
        startFallbackPolling();

        // Check if a user was specified in URL query
        const urlParams = new URLSearchParams(window.location.search);
        const targetUser = urlParams.get('user');
        if (targetUser) {
            setTimeout(() => openUserStatsModal(targetUser), 800);
        }
    }

    function bindEvents() {
        // Server Switcher
        elements.serverSelect.addEventListener('change', (e) => {
            selectGuild(e.target.value);
        });

        // Force Refresh Button
        elements.refreshBtn.addEventListener('click', () => {
            triggerManualRefresh();
        });

        // Search Input & Clear
        elements.channelSearchInput.addEventListener('input', (e) => {
            state.searchQuery = e.target.value;
            elements.clearSearchBtn.classList.toggle('hidden', !state.searchQuery);
            renderChannels();
        });

        elements.clearSearchBtn.addEventListener('click', () => {
            elements.channelSearchInput.value = '';
            state.searchQuery = '';
            elements.clearSearchBtn.classList.add('hidden');
            renderChannels();
        });

        // Filter Mode Buttons
        elements.filterChips.forEach((chip) => {
            chip.addEventListener('click', () => {
                elements.filterChips.forEach((c) => c.classList.remove('active'));
                chip.classList.add('active');
                state.filterMode = chip.dataset.filter;
                renderChannels();
            });
        });

        // Sorting Selector
        elements.sortBySelect.addEventListener('change', (e) => {
            state.sortBy = e.target.value;
            renderChannels();
        });

        // Sidebar Tabs
        elements.sidebarTabBtns.forEach((btn) => {
            btn.addEventListener('click', () => {
                elements.sidebarTabBtns.forEach((b) => b.classList.remove('active'));
                btn.classList.add('active');
                const targetTab = btn.dataset.tab;
                state.currentTab = targetTab;
                Object.keys(elements.tabPanes).forEach((tabKey) => {
                    elements.tabPanes[tabKey].classList.toggle('hidden', tabKey !== targetTab);
                    elements.tabPanes[tabKey].classList.toggle('active', tabKey === targetTab);
                });
                if (targetTab === 'leaderboardTab') {
                    loadLeaderboard();
                    startLeaderboardAutoPoll();
                } else {
                    stopLeaderboardAutoPoll();
                }
            });
        });

        // Clear Activity Feed
        elements.clearFeedBtn.addEventListener('click', () => {
            elements.activityFeedList.innerHTML = '<div class="feed-empty">Activity log cleared.</div>';
        });

        // Reload Leaderboard
        elements.refreshLeaderboardBtn.addEventListener('click', () => {
            loadLeaderboard();
        });

        // Search in Leaderboard
        if (elements.leaderboardSearchInput) {
            elements.leaderboardSearchInput.addEventListener('input', () => {
                renderLeaderboard(state.leaderboardData);
            });
        }

        // Leaderboard Timeframe Buttons (All-Time, Daily, Weekly)
        document.querySelectorAll('.lb-timeframe-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.lb-timeframe-btn').forEach((b) => {
                    b.classList.remove('active');
                    b.style.background = 'var(--bg-secondary)';
                    b.style.color = 'var(--text-secondary)';
                });
                btn.classList.add('active');
                btn.style.background = 'var(--accent-color, #5865F2)';
                btn.style.color = '#fff';
                state.leaderboardTimeframe = btn.dataset.timeframe || 'all';
                loadLeaderboard();
            });
        });

        // Auth Modal Controls
        elements.authModalBtn.addEventListener('click', () => {
            if (state.isAdmin) {
                // Logout action
                if (confirm('Log out of Administrator mode?')) {
                    sessionStorage.removeItem('vc_counter_auth_token');
                    state.authToken = '';
                    state.isAdmin = false;
                    updateAuthButtonUI();
                    showToast('Logged out of admin mode.');
                    selectGuild(state.currentGuildId);
                }
            } else {
                openAuthModal();
            }
        });

        elements.unlockBtn.addEventListener('click', openAuthModal);
        elements.closeModalBtn.addEventListener('click', closeAuthModal);
        elements.cancelAuthBtn.addEventListener('click', closeAuthModal);
        elements.submitAuthBtn.addEventListener('click', submitAuth);
        elements.adminSecretInput.addEventListener('keyup', (e) => {
            if (e.key === 'Enter') submitAuth();
        });

        // User Stats Modal Controls
        elements.closeUserStatsModalBtn.addEventListener('click', closeUserStatsModal);
        elements.userStatsModal.addEventListener('click', (e) => {
            if (e.target === elements.userStatsModal) closeUserStatsModal();
        });

        // Click on Leaderboard row to open member stats
        elements.leaderboardList.addEventListener('click', (e) => {
            const item = e.target.closest('.leaderboard-item');
            if (item && item.dataset.userId) {
                openUserStatsModal(item.dataset.userId);
            }
        });

        // Click on Occupant in channel list to open member stats
        elements.channelsContainer.addEventListener('click', (e) => {
            const item = e.target.closest('.occupant-item');
            if (item && item.dataset.userId) {
                openUserStatsModal(item.dataset.userId);
            }
        });
    }

    // ==============================================================================
    // SERVER SELECTION & API FETCHING
    // ==============================================================================
    async function loadGuilds() {
        try {
            const headers = getAuthHeaders();
            const res = await fetch('/api/guilds', { headers });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);

            const data = await res.json();
            state.guilds = data.guilds || [];

            if (state.guilds.length === 0) {
                elements.serverSelect.innerHTML = '<option value="" disabled selected>No servers available</option>';
                showToast('No servers detected or bot not ready.', 'error');
                return;
            }

            elements.serverSelect.innerHTML = '';
            state.guilds.forEach((g) => {
                const opt = document.createElement('option');
                opt.value = g.id;
                opt.textContent = `${g.name} (${g.member_count} members)`;
                elements.serverSelect.appendChild(opt);
            });

            // Check URL query parameters for ?guild=...
            const urlParams = new URLSearchParams(window.location.search);
            const targetGuildId = urlParams.get('guild');

            if (targetGuildId && state.guilds.some((g) => g.id === targetGuildId)) {
                selectGuild(targetGuildId);
            } else {
                selectGuild(state.guilds[0].id);
            }
        } catch (err) {
            console.error('Failed to load guilds:', err);
            elements.serverSelect.innerHTML = '<option value="" disabled selected>Error loading servers</option>';
            showToast('Failed to load server list.', 'error');
        }
    }

    async function selectGuild(guildId) {
        if (!guildId) return;
        state.currentGuildId = guildId;
        elements.serverSelect.value = guildId;

        // Update server avatar icon in navbar
        const currentGuildObj = state.guilds.find((g) => g.id === guildId);
        if (currentGuildObj && currentGuildObj.icon_url) {
            elements.selectedServerIcon.src = currentGuildObj.icon_url;
            elements.selectedServerIcon.classList.remove('hidden');
        } else {
            elements.selectedServerIcon.classList.add('hidden');
        }

        // Update browser URL query parameter without page reload
        const newUrl = new URL(window.location.href);
        newUrl.searchParams.set('guild', guildId);
        window.history.replaceState({}, '', newUrl);

        // Fetch initial REST stats
        await fetchGuildStats(guildId);

        // Connect or switch WebSocket subscription
        connectWebSocket();

        // Refresh leaderboard
        loadLeaderboard();
    }

    async function fetchGuildStats(guildId) {
        try {
            const headers = getAuthHeaders();
            const res = await fetch(`/api/guilds/${guildId}/stats`, { headers });

            if (res.status === 401) {
                // Guild is private and user is not authenticated
                showPrivateLockState(true);
                return;
            }

            if (!res.ok) throw new Error(`HTTP ${res.status}`);

            showPrivateLockState(false);
            const stats = await res.json();
            updateDashboardData(stats);
        } catch (err) {
            console.error('Error fetching guild statistics:', err);
            showToast('Unable to fetch voice statistics.', 'error');
        }
    }

    async function triggerManualRefresh() {
        if (!state.currentGuildId) return;

        elements.refreshIcon.classList.add('spinning');
        try {
            const headers = getAuthHeaders();
            const res = await fetch(`/api/guilds/${state.currentGuildId}/refresh`, {
                method: 'POST',
                headers,
            });

            if (res.status === 429) {
                showToast('Refresh rate limit reached. Please wait a few seconds.', 'error');
                return;
            }

            if (res.ok) {
                const data = await res.json();
                if (data.stats) updateDashboardData(data.stats);
                showToast('Statistics refreshed successfully.');
            }
        } catch (err) {
            console.error('Refresh failed:', err);
        } finally {
            setTimeout(() => elements.refreshIcon.classList.remove('spinning'), 600);
        }
    }

    async function fetchSystemHealth() {
        try {
            const res = await fetch('/health');
            if (res.ok) {
                const data = await res.json();
                elements.botLatencyValue.textContent = `${data.latency_ms} ms`;
            }
        } catch {
            elements.botLatencyValue.textContent = 'Offline';
        }
    }

    // ==============================================================================
    // REAL-TIME WEBSOCKET SYSTEM
    // ==============================================================================
    function connectWebSocket() {
        if (state.ws) {
            try {
                // If already open and subscribed, just send subscribe frame
                if (state.ws.readyState === WebSocket.OPEN) {
                    state.ws.send(JSON.stringify({
                        type: 'subscribe',
                        guild_id: state.currentGuildId,
                    }));
                    return;
                }
                state.ws.close();
            } catch {
                // Ignore cleanup error
            }
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let wsUrl = `${protocol}//${window.location.host}/ws?guild_id=${state.currentGuildId}`;
        if (state.authToken) {
            wsUrl += `&token=${encodeURIComponent(state.authToken)}`;
        }

        updateConnectionStatus('connecting');

        try {
            state.ws = new WebSocket(wsUrl);

            state.ws.onopen = () => {
                state.wsReconnectAttempts = 0;
                updateConnectionStatus('connected');
                startHeartbeat();
            };

            state.ws.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data);
                    handleWebSocketMessage(message);
                } catch (e) {
                    console.error('Failed to parse WebSocket message:', e);
                }
            };

            state.ws.onclose = (event) => {
                stopHeartbeat();
                updateConnectionStatus('disconnected');
                scheduleWebSocketReconnect();
            };

            state.ws.onerror = (err) => {
                console.warn('WebSocket error observed:', err);
            };
        } catch (err) {
            console.error('WebSocket connection failed to initiate:', err);
            scheduleWebSocketReconnect();
        }
    }

    function scheduleWebSocketReconnect() {
        state.wsReconnectAttempts++;
        // Exponential backoff: 1s, 2s, 4s, 8s, max 15s
        const delay = Math.min(1000 * Math.pow(2, state.wsReconnectAttempts - 1), 15000);
        setTimeout(() => {
            if (state.currentGuildId) {
                connectWebSocket();
            }
        }, delay);
    }

    function startHeartbeat() {
        stopHeartbeat();
        state.wsHeartbeatTimer = setInterval(() => {
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 25000);
    }

    function stopHeartbeat() {
        if (state.wsHeartbeatTimer) {
            clearInterval(state.wsHeartbeatTimer);
            state.wsHeartbeatTimer = null;
        }
    }

    function handleWebSocketMessage(msg) {
        switch (msg.type) {
            case 'connection_established':
                if (msg.authenticated) {
                    state.isAdmin = true;
                    updateAuthButtonUI();
                }
                break;

            case 'initial_state':
            case 'stats_update':
                if (msg.guild_id === state.currentGuildId && msg.data) {
                    updateDashboardData(msg.data);
                    if (state.currentTab === 'leaderboardTab') {
                        loadLeaderboard();
                    }
                }
                break;

            case 'voice_event':
                if (msg.guild_id === state.currentGuildId) {
                    addActivityFeedItem(msg);
                    loadLeaderboard();
                }
                break;

            case 'leaderboard_update':
                if (msg.guild_id === state.currentGuildId && Array.isArray(msg.leaderboard)) {
                    renderLeaderboard(msg.leaderboard);
                }
                break;

            case 'user_stats_update':
                if (msg.guild_id === state.currentGuildId && msg.user_id === state.activeModalUserId && msg.data) {
                    renderUserStatsModalData(msg.data);
                }
                break;

            case 'pong':
                // Heartbeat response acknowledged
                break;

            default:
                break;
        }
    }

    function updateConnectionStatus(status) {
        elements.connectionStatus.className = 'status-pill';
        if (status === 'connected') {
            elements.connectionStatus.classList.add('status-connected');
            elements.connectionText.textContent = 'Live Connected';
        } else if (status === 'connecting') {
            elements.connectionStatus.classList.add('status-connecting');
            elements.connectionText.textContent = 'Reconnecting...';
        } else {
            elements.connectionStatus.classList.add('status-error');
            elements.connectionText.textContent = 'Offline';
        }
    }

    function startFallbackPolling() {
        // Periodic background poll every 30s as safety fallback
        state.fallbackPollTimer = setInterval(() => {
            if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
                if (state.currentGuildId) {
                    fetchGuildStats(state.currentGuildId);
                }
            }
        }, 30000);
    }

    // ==============================================================================
    // DATA RENDERING & UI UPDATES
    // ==============================================================================
    function updateDashboardData(stats) {
        state.currentGuildStats = stats;
        state.lastSyncTimestamp = new Date();

        const counts = stats.counts;

        // Metric Cards
        elements.statTotalMembers.textContent = formatNumber(counts.total_members);
        elements.statHumanMembers.textContent = formatNumber(counts.human_members);
        elements.statBotMembers.textContent = formatNumber(counts.bot_members);

        elements.statInVoice.textContent = formatNumber(counts.in_voice_total);
        elements.statInVoiceHumans.textContent = formatNumber(counts.in_voice_humans);
        elements.statInVoiceBots.textContent = formatNumber(counts.in_voice_bots);

        elements.statActiveVC.textContent = formatNumber(counts.active_voice_channels);
        const percentActive = counts.total_audio_channels > 0
            ? Math.round((counts.active_voice_channels / counts.total_audio_channels) * 100)
            : 0;
        elements.statActivePercent.textContent = `${percentActive}%`;

        elements.statTotalAudio.textContent = formatNumber(counts.total_audio_channels);
        elements.statTotalVoice.textContent = formatNumber(counts.total_voice_channels);
        elements.statTotalStage.textContent = formatNumber(counts.total_stage_channels);

        elements.activeChannelsCountPill.textContent = counts.active_voice_channels;

        // Sidebar info
        elements.counterStatusValue.textContent = stats.has_counters ? 'Configured' : 'Not Setup';
        elements.dashboardModeValue.textContent = stats.is_public ? 'Public' : 'Private (Auth)';

        // Timestamp
        elements.lastUpdatedTimestamp.textContent = new Date().toLocaleTimeString();

        // Render Channel Grid
        renderChannels();
    }

    function renderChannels() {
        if (!state.currentGuildStats || !state.currentGuildStats.channels) {
            elements.channelsContainer.innerHTML = `
                <div class="empty-state">
                    <p>No channel data available.</p>
                </div>
            `;
            return;
        }

        let channels = [...state.currentGuildStats.channels];

        // 1. Filter by mode
        if (state.filterMode === 'active_only') {
            channels = channels.filter((c) => c.member_count > 0);
        } else if (state.filterMode === 'empty_only') {
            channels = channels.filter((c) => c.member_count === 0);
        }

        // 2. Search query filter
        if (state.searchQuery) {
            const q = state.searchQuery.toLowerCase().trim();
            channels = channels.filter((c) => {
                const nameMatch = c.name.toLowerCase().includes(q);
                const categoryMatch = c.category && c.category.toLowerCase().includes(q);
                const occupantMatch = c.occupants && c.occupants.some(
                    (occ) => occ.name.toLowerCase().includes(q) || occ.display_name.toLowerCase().includes(q)
                );
                return nameMatch || categoryMatch || occupantMatch;
            });
        }

        // 3. Sort
        if (state.sortBy === 'occupants_desc') {
            channels.sort((a, b) => b.member_count - a.member_count || a.name.localeCompare(b.name));
        } else if (state.sortBy === 'occupants_asc') {
            channels.sort((a, b) => a.member_count - b.member_count || a.name.localeCompare(b.name));
        } else if (state.sortBy === 'name_asc') {
            channels.sort((a, b) => a.name.localeCompare(b.name));
        } else if (state.sortBy === 'name_desc') {
            channels.sort((a, b) => b.name.localeCompare(a.name));
        }

        elements.channelsCountBadge.textContent = channels.length;

        if (channels.length === 0) {
            elements.channelsContainer.innerHTML = '';
            elements.noChannelsFound.classList.remove('hidden');
            return;
        }

        elements.noChannelsFound.classList.add('hidden');

        // Group channels by category
        const categoryMap = new Map();
        channels.forEach((ch) => {
            const cat = ch.category || 'Uncategorized';
            if (!categoryMap.has(cat)) categoryMap.set(cat, []);
            categoryMap.get(cat).push(ch);
        });

        const fragment = document.createDocumentFragment();

        categoryMap.forEach((categoryChannels, categoryName) => {
            const catBlock = document.createElement('div');
            catBlock.className = 'category-block';

            const catHeader = document.createElement('div');
            catHeader.className = 'category-header';
            catHeader.innerHTML = `
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                <span>${escapeHtml(categoryName)} (${categoryChannels.length})</span>
            `;
            catBlock.appendChild(catHeader);

            const grid = document.createElement('div');
            grid.className = 'channels-grid';

            categoryChannels.forEach((channel) => {
                const card = createChannelCardElement(channel);
                grid.appendChild(card);
            });

            catBlock.appendChild(grid);
            fragment.appendChild(catBlock);
        });

        elements.channelsContainer.innerHTML = '';
        elements.channelsContainer.appendChild(fragment);
    }

    function createChannelCardElement(ch) {
        const card = document.createElement('div');
        const isActive = ch.member_count > 0;
        card.className = `channel-card ${isActive ? 'channel-active' : ''}`;

        const isStage = ch.type === 'stage';
        const typeIcon = isStage
            ? `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg>`
            : `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>`;

        const limitStr = ch.user_limit > 0 ? `/${ch.user_limit}` : '';
        const capacityText = `${ch.member_count}${limitStr}`;

        // Capacity Progress
        const percentFilled = ch.user_limit > 0
            ? Math.min(100, Math.round((ch.member_count / ch.user_limit) * 100))
            : (isActive ? 100 : 0);

        // Occupants List HTML
        let occupantsHtml = '';
        if (ch.occupants && ch.occupants.length > 0) {
            occupantsHtml = ch.occupants.map((occ) => {
                const badges = [];
                if (occ.is_bot) badges.push('<span class="badge-tag badge-bot">BOT</span>');
                if (occ.is_streaming) badges.push('<span class="badge-tag badge-stream">LIVE</span>');
                if (occ.is_video) badges.push('📹');
                if (occ.is_deafened) badges.push('🔕');
                else if (occ.is_muted) badges.push('🔇');

                return `
                    <div class="occupant-item" data-user-id="${escapeHtml(occ.id)}" role="button" tabindex="0" title="Click to view ${escapeHtml(occ.display_name)} voice stats">
                        <div class="occupant-profile">
                            <img class="occupant-avatar" src="${escapeHtml(occ.avatar_url)}" alt="${escapeHtml(occ.display_name)}" loading="lazy">
                            <span class="occupant-name">${escapeHtml(occ.display_name)}</span>
                        </div>
                        <div class="occupant-badges">
                            ${badges.join(' ')}
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            occupantsHtml = `<div class="channel-empty-msg">Channel is empty</div>`;
        }

        card.innerHTML = `
            <div class="channel-card-top">
                <div class="channel-info">
                    <div class="channel-type-icon">${typeIcon}</div>
                    <div class="channel-meta">
                        <div class="channel-name" title="${escapeHtml(ch.name)}">${escapeHtml(ch.name)}</div>
                        <div class="channel-category-label">${escapeHtml(ch.category)}</div>
                    </div>
                </div>
                <div class="channel-capacity-badge ${isActive ? 'has-occupants' : ''}">
                    ${capacityText}
                </div>
            </div>

            <div class="capacity-meter">
                <div class="capacity-bar" style="width: ${percentFilled}%;"></div>
            </div>

            <div class="occupants-list">
                ${occupantsHtml}
            </div>
        `;

        return card;
    }

    function addActivityFeedItem(event) {
        if (!elements.activityFeedList) return;

        // Remove placeholder if present
        const placeholder = elements.activityFeedList.querySelector('.feed-empty');
        if (placeholder) placeholder.remove();

        const item = document.createElement('div');
        item.className = `activity-item activity-${event.action}`;

        let actionText = '';
        if (event.action === 'join') {
            actionText = `joined <strong>${escapeHtml(event.channel)}</strong>`;
        } else if (event.action === 'leave') {
            actionText = `left <strong>${escapeHtml(event.channel)}</strong>`;
        } else if (event.action === 'move') {
            actionText = `moved to <strong>${escapeHtml(event.channel)}</strong>`;
        }

        const timeStr = new Date(event.timestamp * 1000).toLocaleTimeString();
        const avatar = event.avatar_url || 'https://cdn.discordapp.com/embed/avatars/0.png';

        item.innerHTML = `
            <img class="occupant-avatar" src="${escapeHtml(avatar)}" alt="${escapeHtml(event.display_name)}">
            <div style="overflow: hidden;">
                <div><strong>${escapeHtml(event.display_name)}</strong> ${actionText}</div>
                <div class="activity-time">${timeStr}</div>
            </div>
        `;

        elements.activityFeedList.insertBefore(item, elements.activityFeedList.firstChild);

        // Limit feed items in DOM to 50
        while (elements.activityFeedList.children.length > 50) {
            elements.activityFeedList.lastChild.remove();
        }
    }

    function formatLeaderboardTime(seconds) {
        const sec = Math.max(0, Math.floor(seconds || 0));
        const hours = Math.floor(sec / 3600);
        const minutes = Math.floor((sec % 3600) / 60);
        const remainingSeconds = sec % 60;
        if (hours > 0) {
            return `${hours}h ${minutes}m ${remainingSeconds}s`;
        } else if (minutes > 0) {
            return `${minutes}m ${remainingSeconds}s`;
        }
        return `${remainingSeconds}s`;
    }

    function renderLeaderboard(board) {
        if (!Array.isArray(board)) return;
        state.leaderboardData = board;

        if (elements.leaderboardCountBadge) {
            elements.leaderboardCountBadge.textContent = `${board.length} ranked`;
        }

        if (board.length === 0) {
            elements.leaderboardList.innerHTML = '<div class="feed-empty">No voice activity recorded yet. Jump into a voice channel to get on the board!</div>';
            stopLeaderboardLiveTicker();
            return;
        }

        const query = elements.leaderboardSearchInput ? elements.leaderboardSearchInput.value.trim().toLowerCase() : '';
        const filteredBoard = query
            ? board.filter(item => {
                const name = (item.display_name || '').toLowerCase();
                const uName = (item.user_name || '').toLowerCase();
                const uid = String(item.user_id || '');
                return name.includes(query) || uName.includes(query) || uid.includes(query);
            })
            : board;

        if (filteredBoard.length === 0) {
            elements.leaderboardList.innerHTML = `<div class="feed-empty">No members found matching "${escapeHtml(query)}"</div>`;
            return;
        }

        const medals = ['🥇', '🥈', '🥉'];
        elements.leaderboardList.innerHTML = filteredBoard.map((item) => {
            const rank = item.rank || (board.indexOf(item) + 1);
            const medal = rank <= 3 ? medals[rank - 1] : `#${rank}`;
            const timeFormatted = formatLeaderboardTime(item.total_seconds);
            const avatar = item.avatar_url || 'https://cdn.discordapp.com/embed/avatars/0.png';
            const activeBadge = item.is_active ? '<span class="leaderboard-live-badge" title="Currently connected in voice channel">🟢 Live</span>' : '';

            return `
                <div class="leaderboard-item clickable-member ${item.is_active ? 'is-in-vc' : ''}" data-user-id="${escapeHtml(item.user_id)}" role="button" tabindex="0" title="Click to view ${escapeHtml(item.display_name)} voice stats">
                    <span class="leaderboard-rank">${medal}</span>
                    <div class="leaderboard-user">
                        <img class="occupant-avatar" src="${escapeHtml(avatar)}" alt="${escapeHtml(item.display_name)}">
                        <span class="occupant-name">${escapeHtml(item.display_name)}</span>
                        ${activeBadge}
                    </div>
                    <span class="leaderboard-time" id="lb-time-${escapeHtml(item.user_id)}">${timeFormatted}</span>
                </div>
            `;
        }).join('');

        const hasActive = board.some(item => item.is_active);
        if (hasActive) {
            startLeaderboardLiveTicker();
        } else {
            stopLeaderboardLiveTicker();
        }
    }

    function startLeaderboardLiveTicker() {
        if (state.leaderboardLiveTicker) return;
        state.leaderboardLiveTicker = setInterval(() => {
            if (!state.leaderboardData || state.leaderboardData.length === 0) return;

            state.leaderboardData.forEach(item => {
                if (item.is_active) {
                    item.total_seconds = (item.total_seconds || 0) + 1;
                    const el = document.getElementById(`lb-time-${item.user_id}`);
                    if (el) {
                        el.textContent = formatLeaderboardTime(item.total_seconds);
                    }
                }
            });
        }, 1000);
    }

    function stopLeaderboardLiveTicker() {
        if (state.leaderboardLiveTicker) {
            clearInterval(state.leaderboardLiveTicker);
            state.leaderboardLiveTicker = null;
        }
    }

    function startLeaderboardAutoPoll() {
        stopLeaderboardAutoPoll();
        state.leaderboardAutoPollTimer = setInterval(() => {
            if (state.currentTab === 'leaderboardTab' && state.currentGuildId) {
                loadLeaderboard();
            }
        }, 3000);
    }

    function stopLeaderboardAutoPoll() {
        if (state.leaderboardAutoPollTimer) {
            clearInterval(state.leaderboardAutoPollTimer);
            state.leaderboardAutoPollTimer = null;
        }
    }

    async function loadLeaderboard() {
        if (!state.currentGuildId) return;

        try {
            const headers = getAuthHeaders();
            const tf = state.leaderboardTimeframe || 'all';
            const res = await fetch(`/api/guilds/${state.currentGuildId}/leaderboard?timeframe=${encodeURIComponent(tf)}`, { headers });
            if (!res.ok) return;

            const data = await res.json();
            const board = data.leaderboard || [];
            renderLeaderboard(board);
        } catch (e) {
            console.error('Error loading leaderboard:', e);
        }
    }

    // ==============================================================================
    // USER VOICE STATS MODAL & REAL-TIME TICKER
    // ==============================================================================
    async function openUserStatsModal(userId) {
        if (!userId || !state.currentGuildId) return;

        state.activeModalUserId = userId;
        elements.userStatsModal.classList.remove('hidden');

        // Initial skeleton / loading values
        elements.userStatsDisplayName.textContent = 'Loading Stats...';
        elements.userStatsStatusBadge.className = 'status-badge-pill';
        elements.userStatsStatusBadge.textContent = 'Fetching breakdown...';
        elements.userStatsLiveDot.classList.add('hidden');
        elements.userStatsTotalTime.textContent = '...';

        try {
            const headers = getAuthHeaders();
            const res = await fetch(`/api/guilds/${state.currentGuildId}/users/${userId}/stats`, { headers });
            if (!res.ok) {
                showToast('Unable to load user stats', 'error');
                return;
            }
            const data = await res.json();
            renderUserStatsModalData(data);
        } catch (err) {
            console.error('Error fetching user voice stats:', err);
            showToast('Network error loading member stats', 'error');
        }
    }

    function renderUserStatsModalData(data) {
        if (!data) return;
        state.modalUserData = data;

        // Avatar & Identity
        elements.userStatsAvatar.src = data.avatar_url || 'https://cdn.discordapp.com/embed/avatars/0.png';
        elements.userStatsDisplayName.textContent = data.display_name || data.user_name || 'Member';

        // Online / Voice state indicator
        if (data.is_connected) {
            elements.userStatsLiveDot.classList.remove('hidden');
            elements.userStatsStatusBadge.className = 'status-badge-pill status-online';
            const stateDisplay = (data.current_state || 'in voice').toUpperCase();
            const channelName = data.current_channel_name ? `in #${data.current_channel_name}` : 'in voice';
            elements.userStatsStatusBadge.textContent = `🔊 ${stateDisplay} ${channelName}`;
            startUserStatsTicker();
        } else {
            elements.userStatsLiveDot.classList.add('hidden');
            elements.userStatsStatusBadge.className = 'status-badge-pill status-offline';
            elements.userStatsStatusBadge.textContent = 'Offline / Not in Voice';
            stopUserStatsTicker();
        }

        updateUserStatsModalDisplay();
    }

    function updateUserStatsModalDisplay() {
        const data = state.modalUserData;
        if (!data) return;

        // Total Banner
        const allTimeTotal = (data.all_time && data.all_time.total !== undefined) ? data.all_time.total : 0;
        elements.userStatsTotalTime.textContent = formatDuration(allTimeTotal);

        // All Time Breakdown
        if (data.all_time) {
            elements.userAllTimeUnmuted.textContent = formatDuration(data.all_time.unmuted || 0);
            elements.userAllTimeMuted.textContent = formatDuration(data.all_time.muted || 0);
            elements.userAllTimeDeafened.textContent = formatDuration(data.all_time.deafened || 0);
            elements.userAllTimeAfk.textContent = formatDuration(data.all_time.afk || 0);
        }

        // Daily Breakdown
        if (data.daily) {
            const dailyTotal = data.daily.total !== undefined ? data.daily.total : 0;
            elements.userDailyTotal.textContent = `Total: ${formatDuration(dailyTotal)}`;
            elements.userDailyUnmuted.textContent = formatDuration(data.daily.unmuted || 0);
            elements.userDailyMuted.textContent = formatDuration(data.daily.muted || 0);
            elements.userDailyDeafened.textContent = formatDuration(data.daily.deafened || 0);
            elements.userDailyAfk.textContent = formatDuration(data.daily.afk || 0);
        }

        // Weekly Breakdown
        if (data.weekly) {
            const weeklyTotal = data.weekly.total !== undefined ? data.weekly.total : 0;
            elements.userWeeklyTotal.textContent = `Total: ${formatDuration(weeklyTotal)}`;
            elements.userWeeklyUnmuted.textContent = formatDuration(data.weekly.unmuted || 0);
            elements.userWeeklyMuted.textContent = formatDuration(data.weekly.muted || 0);
            elements.userWeeklyDeafened.textContent = formatDuration(data.weekly.deafened || 0);
            elements.userWeeklyAfk.textContent = formatDuration(data.weekly.afk || 0);
        }
    }

    function startUserStatsTicker() {
        stopUserStatsTicker();
        state.userStatsLiveTicker = setInterval(() => {
            const data = state.modalUserData;
            if (!data || !data.is_connected || !data.current_state) return;

            const st = data.current_state; // 'unmuted', 'muted', 'deafened', or 'afk'
            if (data.all_time) {
                data.all_time[st] = (data.all_time[st] || 0) + 1;
                data.all_time.total = (data.all_time.total || 0) + 1;
            }
            if (data.daily) {
                data.daily[st] = (data.daily[st] || 0) + 1;
                data.daily.total = (data.daily.total || 0) + 1;
            }
            if (data.weekly) {
                data.weekly[st] = (data.weekly[st] || 0) + 1;
                data.weekly.total = (data.weekly.total || 0) + 1;
            }
            updateUserStatsModalDisplay();
        }, 1000);
    }

    function stopUserStatsTicker() {
        if (state.userStatsLiveTicker) {
            clearInterval(state.userStatsLiveTicker);
            state.userStatsLiveTicker = null;
        }
    }

    function closeUserStatsModal() {
        elements.userStatsModal.classList.add('hidden');
        state.activeModalUserId = null;
        state.modalUserData = null;
        stopUserStatsTicker();
    }

    function formatDuration(seconds) {
        const sec = Math.max(0, Math.floor(seconds || 0));
        const hours = Math.floor(sec / 3600);
        const minutes = Math.floor((sec % 3600) / 60);
        const remainingSeconds = sec % 60;
        return `${hours}h ${minutes}m ${remainingSeconds}s`;
    }

    // ==============================================================================
    // AUTHENTICATION MODAL & PRIVACY STATE
    // ==============================================================================
    function openAuthModal() {
        elements.authModal.classList.remove('hidden');
        elements.adminSecretInput.value = '';
        elements.modalAuthFeedback.classList.add('hidden');
        elements.adminSecretInput.focus();
    }

    function closeAuthModal() {
        elements.authModal.classList.add('hidden');
    }

    async function submitAuth() {
        const token = elements.adminSecretInput.value.trim();
        if (!token) return;

        elements.submitAuthBtn.disabled = true;
        elements.submitAuthBtn.textContent = 'Verifying...';

        try {
            const res = await fetch('/api/auth/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token }),
            });

            if (res.ok) {
                sessionStorage.setItem('vc_counter_auth_token', token);
                state.authToken = token;
                state.isAdmin = true;
                updateAuthButtonUI();

                elements.modalAuthFeedback.className = 'auth-feedback success';
                elements.modalAuthFeedback.textContent = 'Authentication successful! Administrator mode granted.';
                elements.modalAuthFeedback.classList.remove('hidden');

                setTimeout(() => {
                    closeAuthModal();
                    showPrivateLockState(false);
                    selectGuild(state.currentGuildId);
                }, 800);
            } else {
                elements.modalAuthFeedback.className = 'auth-feedback error';
                elements.modalAuthFeedback.textContent = 'Invalid administrator secret token.';
                elements.modalAuthFeedback.classList.remove('hidden');
            }
        } catch (err) {
            elements.modalAuthFeedback.className = 'auth-feedback error';
            elements.modalAuthFeedback.textContent = 'Network error while attempting authentication.';
            elements.modalAuthFeedback.classList.remove('hidden');
        } finally {
            elements.submitAuthBtn.disabled = false;
            elements.submitAuthBtn.textContent = 'Authenticate';
        }
    }

    function updateAuthButtonUI() {
        if (state.isAdmin) {
            elements.authBtnLabel.textContent = 'Admin (Active)';
            elements.authModalBtn.classList.remove('btn-primary');
            elements.authModalBtn.classList.add('btn-secondary');
        } else {
            elements.authBtnLabel.textContent = 'Admin Login';
            elements.authModalBtn.classList.add('btn-primary');
            elements.authModalBtn.classList.remove('btn-secondary');
        }
    }

    function showPrivateLockState(isLocked) {
        if (isLocked) {
            elements.privateLockBanner.classList.remove('hidden');
            elements.metricsSection.classList.add('hidden');
            elements.channelsContainer.innerHTML = '';
        } else {
            elements.privateLockBanner.classList.add('hidden');
            elements.metricsSection.classList.remove('hidden');
        }
    }

    function getAuthHeaders() {
        const headers = { 'Accept': 'application/json' };
        if (state.authToken) {
            headers['Authorization'] = `Bearer ${state.authToken}`;
        }
        return headers;
    }

    // ==============================================================================
    // UTILITY HELPERS
    // ==============================================================================
    function formatNumber(num) {
        if (num === undefined || num === null || isNaN(num)) return '0';
        return Number(num).toLocaleString();
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type === 'error' ? 'toast-error' : ''}`;
        toast.textContent = message;
        elements.toastContainer.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, 3200);
    }

    // Bootstrap app on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
