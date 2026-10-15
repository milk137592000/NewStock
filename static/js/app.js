// 全域變數
let activeTab = 'dashboard';
let activeChartSymbol = 'TWII';
let priceChartInstance = null;
let kdChartInstance = null;
let pollingInterval = null;

// 頁面載入完成後初始化
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initEventListeners();
    fetchConfig();
    fetchStatus();
    
    // 每 60 秒輪詢一次最新狀態 (因為後端有 1 分鐘快取，輪詢不會造成效能壓力)
    pollingInterval = setInterval(fetchStatus, 60000);
});

// 1. 初始化頁籤導覽
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const tabContents = document.querySelectorAll('.tab-content');
    const pageTitle = document.getElementById('page-title');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const tabId = item.getAttribute('data-tab');
            
            // 更新 active 狀態
            navItems.forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            
            // 切換區塊顯示
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === `tab-${tabId}`) {
                    content.classList.add('active');
                }
            });
            
            activeTab = tabId;
            
            // 更新頁面標題
            if (tabId === 'dashboard') {
                pageTitle.textContent = '即時監控儀表板';
                fetchStatus(); // 切回首頁時順便刷一下
            } else if (tabId === 'biweekly') {
                pageTitle.textContent = '長線標的雙週精選池';
                loadBiweeklyData();
            } else if (tabId === 'charts') {
                pageTitle.textContent = '技術分析圖表';
                loadChartData(activeChartSymbol);
            } else if (tabId === 'settings') {
                pageTitle.textContent = '系統與警報設定';
                fetchConfig();
            }
        });
    });
}

// 2. 註冊事件監聽
function initEventListeners() {
    // 立即重新整理按鈕
    document.getElementById('btn-sync').addEventListener('click', async () => {
        const btn = document.getElementById('btn-sync');
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 處理中...';
        
        try {
            const res = await fetch('/api/trigger', { method: 'POST' });
            const data = await res.json();
            if (data.success) {
                fetchStatus();
                if (activeTab === 'charts') {
                    loadChartData(activeChartSymbol);
                }
                showNotification('資料重新整理成功！', 'success');
            }
        } catch (err) {
            console.error(err);
            showNotification('重新整理失敗，請檢查後端服務。', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-rotate"></i> 立即重新整理';
        }
    });

    // 圖表頁籤切換
    const chartTabBtns = document.querySelectorAll('.chart-selector .btn-tab');
    chartTabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            chartTabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            const symbol = btn.getAttribute('data-chart');
            activeChartSymbol = symbol;
            
            document.getElementById('chart-title').textContent = `${btn.textContent} 歷史走勢`;
            loadChartData(symbol);
        });
    });

    // 儲存設定表單提交
    document.getElementById('form-settings').addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('btn-save-settings');
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 儲存中...';

        const configPayload = {
            LINE_CHANNEL_ACCESS_TOKEN: document.getElementById('line-token').value,
            LINE_USER_ID: document.getElementById('line-userid').value,
            DROP_THRESHOLD: parseFloat(document.getElementById('drop-threshold').value),
            DROP_STEP: parseFloat(document.getElementById('drop-step').value),
            USE_KD_STRATEGY: document.getElementById('use-kd').checked,
            USE_BOLLINGER_STRATEGY: document.getElementById('use-bollinger').checked,
            KD_LIMIT: parseFloat(document.getElementById('kd-limit').value)
        };

        try {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(configPayload)
            });
            const data = await res.json();
            if (data.success) {
                showNotification('設定更新成功！已立即套用新參數並重新整理數據。', 'success');
                fetchConfig(); // 重新讀取，使 token 顯示為 Mask 格式
            } else {
                showNotification(`儲存失敗: ${data.detail}`, 'error');
            }
        } catch (err) {
            console.error(err);
            showNotification('連線後端 API 異常。', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> 儲存設定';
        }
    });

    // 發送測試通知按鈕
    document.getElementById('btn-test-line').addEventListener('click', async () => {
        const btn = document.getElementById('btn-test-line');
        const statusDiv = document.getElementById('test-status');
        const testMsg = document.getElementById('test-message').value;

        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 發送中...';
        statusDiv.className = 'test-status-msg';
        statusDiv.textContent = '正在發送測試訊息至 LINE...';

        try {
            const res = await fetch('/api/test_line', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: testMsg })
            });
            const data = await res.json();
            if (data.success) {
                statusDiv.className = 'test-status-msg success';
                statusDiv.innerHTML = '<i class="fa-solid fa-check"></i> ' + data.message;
            } else {
                statusDiv.className = 'test-status-msg error';
                statusDiv.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> ' + data.message;
            }
        } catch (err) {
            console.error(err);
            statusDiv.className = 'test-status-msg error';
            statusDiv.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> 連線失敗，請檢查後端是否正常運作及 .env 設定。';
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-brands fa-line"></i> 發送測試通知';
        }
    });

    // 觸發雙週長線分析推播按鈕
    const btnBiweekly = document.getElementById('btn-trigger-biweekly');
    if (btnBiweekly) {
        btnBiweekly.addEventListener('click', async () => {
            btnBiweekly.disabled = true;
            btnBiweekly.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 正在分析並推播中...';
            try {
                const res = await fetch('/api/analysis/biweekly/trigger', { method: 'POST' });
                const json = await res.json();
                if (json.success) {
                    showNotification('長線標的雙週分析已成功產出並推播至 LINE！', 'success');
                    renderBiweeklyContent(json.data);
                } else {
                    showNotification('推播失敗: ' + (json.detail || '未知錯誤'), 'error');
                }
            } catch (err) {
                console.error(err);
                showNotification('連線 API 異常。', 'error');
            } finally {
                btnBiweekly.disabled = false;
                btnBiweekly.innerHTML = '<i class="fa-brands fa-line"></i> 立即推送雙週分析至 LINE';
            }
        });
    }
}

// 3. 獲取並呈現設定值
async function fetchConfig() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        
        document.getElementById('line-token').value = data.LINE_CHANNEL_ACCESS_TOKEN_MASKED || '';
        document.getElementById('line-userid').value = data.LINE_USER_ID || '';
        document.getElementById('drop-threshold').value = data.DROP_THRESHOLD || 500;
        document.getElementById('drop-step').value = data.DROP_STEP || 100;
        document.getElementById('use-kd').checked = data.USE_KD_STRATEGY;
        document.getElementById('use-bollinger').checked = data.USE_BOLLINGER_STRATEGY;
        document.getElementById('kd-limit').value = data.KD_LIMIT || 20;
    } catch (err) {
        console.error('讀取設定失敗:', err);
    }
}

// 4. 獲取並渲染主 Dashboard 狀態
async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        const json = await res.json();
        
        if (!json.success || !json.data) return;
        const result = json.data;
        
        // 渲染更新時間
        const syncText = document.getElementById('last-sync-text');
        if (json.last_update) {
            const dateObj = new Date(json.last_update);
            syncText.textContent = `最後更新時間：${dateObj.toLocaleDateString()} ${dateObj.toLocaleTimeString()}`;
        }
        
        // 渲染大盤卡片
        const twiiPrice = document.getElementById('twii-price');
        const twiiChange = document.getElementById('twii-change');
        const twiiTodayDrop = document.getElementById('twii-today-drop');
        const twiiCumulativeDrop = document.getElementById('twii-cumulative-drop');
        const twiiWaveHigh = document.getElementById('twii-wave-high');
        
        const dropInfo = result.drop_info || {};
        if (dropInfo.current_index) {
            const index = dropInfo.current_index;
            const diff = index - dropInfo.yesterday_close;
            const pct = (diff / dropInfo.yesterday_close) * 100;
            
            twiiPrice.textContent = index.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            
            const diffSign = diff >= 0 ? '+' : '';
            twiiChange.textContent = `${diffSign}${diff.toFixed(2)} (${diffSign}${pct.toFixed(2)}%)`;
            twiiChange.className = `price-change ${diff >= 0 ? 'up' : 'down'}`;
            
            twiiTodayDrop.textContent = `${dropInfo.today_drop.toFixed(2)} 點`;
            twiiTodayDrop.className = dropInfo.today_drop > 0 ? 'text-danger' : 'text-success';
            
            if (twiiCumulativeDrop && dropInfo.cumulative_drop !== undefined) {
                twiiCumulativeDrop.textContent = `${dropInfo.cumulative_drop.toFixed(2)} 點`;
                twiiCumulativeDrop.className = dropInfo.cumulative_drop > 0 ? 'text-danger' : 'text-success';
            }
        }
        
        if (result.state) {
            twiiWaveHigh.textContent = `${result.state.wave_high.toLocaleString(undefined, { maximumFractionDigits: 2 })} 點`;
            
            // 渲染警報 Badge 狀態
            const bDrop500 = document.getElementById('badge-drop-500');
            const todayNotified = result.state.today_notified_drops || [];
            if (todayNotified.length > 0) {
                bDrop500.className = 'badge triggered';
                bDrop500.textContent = '已觸發';
                document.getElementById('list-today-drops').textContent = todayNotified.map(n => `-${n}`).join(', ');
            } else {
                bDrop500.className = 'badge';
                bDrop500.textContent = '未觸發';
                document.getElementById('list-today-drops').textContent = '無';
            }
            
            const bWaveDrop = document.getElementById('badge-wave-drop');
            const waveNotified = result.state.wave_notified_drops || [];
            if (waveNotified.length > 0) {
                bWaveDrop.className = 'badge triggered';
                bWaveDrop.textContent = '已觸發';
                document.getElementById('list-wave-drops').textContent = waveNotified.map(n => `-${n}`).join(', ');
            } else {
                bWaveDrop.className = 'badge';
                bWaveDrop.textContent = '未觸發';
                document.getElementById('list-wave-drops').textContent = '無';
            }
        }
        
        // 渲染所有 ETF 卡片
        const etfInfo = result.etf_info || {};
        const etfIds = ['0050', '00646', '00692', '00850', '00662', '00830', '00878'];
        etfIds.forEach(id => {
            updateEtfCard(id, etfInfo[id], result.state?.yesterday_close);
        });
        
        // 渲染通知紀錄
        updateNotificationsLog(result.notified_messages, result.state?.date);
        
    } catch (err) {
        console.error('獲取狀態失敗:', err);
    }
}

// 輔助函數：更新 ETF 卡片
function updateEtfCard(id, etfData, refClose) {
    if (!etfData) return;
    
    const priceEl = document.getElementById(`${id}-price`);
    const changeEl = document.getElementById(`${id}-change`);
    const kdEl = document.getElementById(`${id}-kd`);
    const badgeEl = document.getElementById(`${id}-signal-badge`);
    const dropEl = document.getElementById(`${id}-drop52w`);
    const cooldownEl = document.getElementById(`${id}-cooldown`);
    
    if (priceEl) priceEl.textContent = etfData.price.toFixed(2);
    
    // 顯示日線 KD
    if (kdEl) kdEl.textContent = `K: ${etfData.K.toFixed(1)} / D: ${etfData.D.toFixed(1)}`;
    
    // 52 週高點跌幅
    if (dropEl && etfData.drop_from_high_pct !== undefined) {
        const drop = etfData.drop_from_high_pct;
        dropEl.textContent = `-${drop.toFixed(1)}%`;
        if (drop >= 20) {
            dropEl.className = 'drop-value drop-severe';
        } else if (drop >= 15) {
            dropEl.className = 'drop-value drop-high';
        } else if (drop >= 10) {
            dropEl.className = 'drop-value drop-mid';
        } else {
            dropEl.className = 'drop-value drop-low';
        }
    }
    
    // 冷卻期狀態
    if (cooldownEl) {
        if (etfData.in_cooldown) {
            cooldownEl.innerHTML = `<i class="fa-solid fa-clock"></i> 冷卻中 (至 ${etfData.cooldown_until || '?'})`;
            cooldownEl.className = 'cooldown-status cooling';
        } else {
            cooldownEl.innerHTML = '<i class="fa-solid fa-check-circle"></i> 可加碼';
            cooldownEl.className = 'cooldown-status ready';
        }
    }
    
    // 乖離率
    if (changeEl) {
        const deviation = ((etfData.price - etfData.MA) / etfData.MA) * 100;
        changeEl.textContent = `乖離率: ${deviation >= 0 ? '+' : ''}${deviation.toFixed(2)}% (vs 20MA)`;
        changeEl.className = `price-change ${deviation >= 0 ? 'up' : 'down'}`;
    }
    
    // 進場訊號 Badge
    if (badgeEl) {
        if (etfData.signal) {
            badgeEl.className = 'signal-badge buy';
            badgeEl.textContent = '買進訊號';
        } else {
            badgeEl.className = 'signal-badge';
            badgeEl.textContent = '無訊號';
        }
    }
}

// 輔助函數：更新通知紀錄 Log
function updateNotificationsLog(messages, dateStr) {
    const container = document.getElementById('log-list');
    if (!messages || messages.length === 0) {
        container.innerHTML = '<div class="log-empty">尚無今日通知紀錄。</div>';
        return;
    }
    
    let html = '';
    messages.forEach((msg) => {
        let typeClass = 'info';
        let icon = '<i class="fa-solid fa-circle-info"></i>';
        
        if (msg.includes('暴跌') || msg.includes('累積下跌')) {
            typeClass = 'drop';
            icon = '<i class="fa-solid fa-triangle-exclamation text-danger"></i>';
        } else if (msg.includes('進場訊號')) {
            typeClass = 'signal';
            icon = '<i class="fa-solid fa-circle-up text-success"></i>';
        }
        
        // 簡單提取時間，或用當前時間
        const timeStr = new Date().toLocaleTimeString();
        
        // 清洗換行符
        const formattedMsg = msg.replace(/\n/g, '<br>');
        
        html += `
            <div class="log-item ${typeClass}">
                <span class="log-time">${icon} ${dateStr || ''} ${timeStr}</span>
                <div>${formattedMsg}</div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// 5. 載入圖表數據並渲染 Chart.js
async function loadChartData(symbol) {
    try {
        const res = await fetch(`/api/chart/${symbol}`);
        const json = await res.json();
        
        if (!json.success || !json.data) return;
        const chartData = json.data;
        
        renderStockChart(symbol, chartData);
    } catch (err) {
        console.error('圖表載入失敗:', err);
    }
}

// 6. 渲染 Chart.js 圖表
function renderStockChart(symbol, chartData) {
    const priceCtx = document.getElementById('stock-chart').getContext('2d');
    const kdContainer = document.getElementById('kd-chart-container');
    
    // 銷毀舊圖表
    if (priceChartInstance) priceChartInstance.destroy();
    if (kdChartInstance) kdChartInstance.destroy();
    
    // 常規圖表設定
    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                labels: { color: '#94a3b8', font: { family: 'Outfit' } }
            },
            tooltip: {
                mode: 'index',
                intersect: false,
                backgroundColor: 'rgba(15, 23, 42, 0.9)',
                titleColor: '#fff',
                bodyColor: '#cbd5e1',
                borderColor: 'rgba(255,255,255,0.1)',
                borderWidth: 1
            }
        },
        scales: {
            x: {
                grid: { color: 'rgba(255, 255, 255, 0.03)' },
                ticks: { color: '#64748b', font: { family: 'Outfit' } }
            },
            y: {
                grid: { color: 'rgba(255, 255, 255, 0.03)' },
                ticks: { color: '#64748b', font: { family: 'Outfit' } }
            }
        }
    };
    
    // 準備 datasets
    const datasets = [
        {
            label: `${symbol} 收盤價`,
            data: chartData.prices,
            borderColor: '#6366f1',
            backgroundColor: 'rgba(99, 102, 241, 0.05)',
            borderWidth: 2.5,
            fill: true,
            tension: 0.15
        }
    ];
    
    // 如果是 ETF，加入布林通道
    if (symbol !== 'TWII' && chartData.MA) {
        datasets.push({
            label: '20MA',
            data: chartData.MA,
            borderColor: '#94a3b8',
            borderWidth: 1.5,
            borderDash: [5, 5],
            fill: false,
            pointStyle: 'none',
            pointRadius: 0
        });
        datasets.push({
            label: '布林上軌',
            data: chartData.Upper,
            borderColor: 'rgba(14, 165, 233, 0.4)',
            borderWidth: 1,
            fill: false,
            pointStyle: 'none',
            pointRadius: 0
        });
        datasets.push({
            label: '布林下軌',
            data: chartData.Lower,
            borderColor: 'rgba(245, 158, 11, 0.4)',
            borderWidth: 1,
            fill: false,
            pointStyle: 'none',
            pointRadius: 0
        });
        
        // 顯示 KD 圖表容器
        kdContainer.style.display = 'block';
        
        // 建立價格圖表
        priceChartInstance = new Chart(priceCtx, {
            type: 'line',
            data: { labels: chartData.labels, datasets: datasets },
            options: commonOptions
        });
        
        // 建立 KD 圖表
        const kdCtx = document.getElementById('kd-chart').getContext('2d');
        kdChartInstance = new Chart(kdCtx, {
            type: 'line',
            data: {
                labels: chartData.labels,
                datasets: [
                    {
                        label: 'K 值 (9)',
                        data: chartData.K,
                        borderColor: '#ef4444', // 紅色 K 線
                        borderWidth: 2,
                        pointRadius: 1,
                        tension: 0.15,
                        fill: false
                    },
                    {
                        label: 'D 值 (3)',
                        data: chartData.D,
                        borderColor: '#3b82f6', // 藍色 D 線
                        borderWidth: 2,
                        pointRadius: 1,
                        tension: 0.15,
                        fill: false
                    }
                ]
            },
            options: {
                ...commonOptions,
                scales: {
                    x: commonOptions.scales.x,
                    y: {
                        min: 0,
                        max: 100,
                        grid: { color: 'rgba(255, 255, 255, 0.03)' },
                        ticks: { color: '#64748b', stepSize: 20 }
                    }
                }
            }
        });
    } else {
        // 大盤，隱藏 KD 圖表
        kdContainer.style.display = 'none';
        
        priceChartInstance = new Chart(priceCtx, {
            type: 'line',
            data: { labels: chartData.labels, datasets: datasets },
            options: commonOptions
        });
    }
}

// 輔助工具：顯示 Alert 通知
function showNotification(msg, type = 'success') {
    // 簡易建立 Toast 元素
    const toast = document.createElement('div');
    toast.className = `toast-notification ${type}`;
    toast.innerHTML = `
        <div class="toast-content">
            <i class="fa-solid ${type === 'success' ? 'fa-circle-check text-success' : 'fa-circle-xmark text-danger'}"></i>
            <span>${msg}</span>
        </div>
    `;
    
    // 設置 Toast 樣式 (臨時加入 body，以 inline 方式提供避免修改 css)
    Object.assign(toast.style, {
        position: 'fixed',
        bottom: '20px',
        right: '20px',
        background: 'rgba(15, 23, 42, 0.95)',
        border: '1px solid rgba(255,255,255,0.1)',
        padding: '1rem 1.5rem',
        borderRadius: '12px',
        boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
        zIndex: '9999',
        backdropFilter: 'blur(8px)',
        transform: 'translateY(100px)',
        opacity: '0',
        transition: 'all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)'
    });
    
    document.body.appendChild(toast);
    
    // 觸發動畫
    setTimeout(() => {
        toast.style.transform = 'translateY(0)';
        toast.style.opacity = '1';
    }, 100);
    
    // 3 秒後銷毀
    setTimeout(() => {
        toast.style.transform = 'translateY(100px)';
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 400);
    }, 3000);
}

// 7. 長線標的雙週分析資料獲取與渲染
async function loadBiweeklyData() {
    const container = document.getElementById('biweekly-content');
    if (!container) return;
    
    container.innerHTML = '<div class="loading-state"><i class="fa-solid fa-spinner fa-spin"></i> 載入長線雙週分析數據中...</div>';
    
    try {
        const res = await fetch('/api/analysis/biweekly');
        const json = await res.json();
        if (json.success && json.data) {
            renderBiweeklyContent(json.data);
        } else {
            container.innerHTML = `<div class="error-state">載入失敗: ${json.detail || '無法取得分析數據'}</div>`;
        }
    } catch (err) {
        console.error('載入雙週分析失敗:', err);
        container.innerHTML = '<div class="error-state">連線異常，請稍後重試。</div>';
    }
}

function renderBiweeklyContent(data) {
    const container = document.getElementById('biweekly-content');
    if (!container) return;
    
    const results = data.results || [];
    const top3 = data.top3 || [];
    
    // Top 3 推薦卡片 HTML
    let top3Html = '';
    top3.forEach((item, idx) => {
        const rank = idx + 1;
        const dropClass = item.drop_pct >= 20 ? 'drop-severe' : (item.drop_pct >= 15 ? 'drop-high' : (item.drop_pct >= 10 ? 'drop-mid' : 'drop-low'));
        top3Html += `
            <div class="top3-card rank-${rank}">
                <div class="top3-rank-badge">NO.${rank} 首選</div>
                <div class="top3-name">${item.name} (${item.code})</div>
                <div class="top3-theme"><i class="fa-solid fa-tag"></i> ${item.theme}</div>
                <div class="top3-tier">${item.tier_label}</div>
                <div class="top3-metrics">
                    <span>市價: <b>${item.price.toFixed(2)}</b></span>
                    <span>距52週高: <b class="${dropClass}">-${item.drop_pct.toFixed(1)}%</b></span>
                    <span>週KD: <b>${item.weekly_k.toFixed(1)}/${item.weekly_d.toFixed(1)}</b></span>
                </div>
            </div>
        `;
    });
    
    // 全清單表格 HTML
    let tableRows = '';
    results.forEach(item => {
        const dropClass = item.drop_pct >= 20 ? 'drop-severe' : (item.drop_pct >= 15 ? 'drop-high' : (item.drop_pct >= 10 ? 'drop-mid' : 'drop-low'));
        const typeBadge = item.type === '觀察候選' 
            ? '<span class="card-tag group-low">觀察候選</span>' 
            : '<span class="card-tag group-mid">現有核心</span>';
        
        tableRows += `
            <tr>
                <td><b>${item.code}</b></td>
                <td>${item.name}</td>
                <td>${typeBadge}</td>
                <td>${item.group_label}</td>
                <td>$${item.price.toFixed(2)}</td>
                <td>$${item.high_52w.toFixed(2)}</td>
                <td class="${dropClass}"><b>-${item.drop_pct.toFixed(1)}%</b></td>
                <td>${item.weekly_k.toFixed(1)} / ${item.weekly_d.toFixed(1)}</td>
                <td>${item.tier_label}</td>
            </tr>
        `;
    });
    
    container.innerHTML = `
        <h3 class="biweekly-section-title"><i class="fa-solid fa-fire text-danger"></i> 本期逢低加碼重點推薦 Top 3</h3>
        <div class="biweekly-top3-grid">
            ${top3Html}
        </div>
        
        <h3 class="biweekly-section-title"><i class="fa-solid fa-list-check text-info"></i> 全部長期持有與候選標的健康度概覽 (${results.length} 檔)</h3>
        <div class="biweekly-table-container">
            <table class="biweekly-table">
                <thead>
                    <tr>
                        <th>代號</th>
                        <th>標的名稱</th>
                        <th>屬性</th>
                        <th>配置分組</th>
                        <th>目前市價</th>
                        <th>52週高點</th>
                        <th>距高點跌幅</th>
                        <th>週 KD (9W)</th>
                        <th>加碼建議位階</th>
                    </tr>
                </thead>
                <tbody>
                    ${tableRows}
                </tbody>
            </table>
        </div>
    `;
}
