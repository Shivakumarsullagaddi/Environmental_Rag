/**
 * Environmental AI Scientist — Frontend Application Logic
 * 
 * Vanilla JavaScript managing:
 * - ADK Session-mapped conversations (Left Sidebar)
 * - Markdown & structured scientific responses (Main Chat)
 * - Sequential/Parallel Tool Execution Trace (Right Panel)
 * - Grounded Chart.js Visualizations (Chart Window)
 */

document.addEventListener("DOMContentLoaded", () => {
  // --------------------------------------------------------------------------
  // Application State
  // --------------------------------------------------------------------------
  const state = {
    activeSessionId: null,
    conversations: [],
    isLoading: false,
    chartInstance: null,
  };

  // Active requests registry: routes SSE events deterministically to target assistant messages
  const activeRequests = new Map();

  // ── DEBUG: concurrency counter ────────────────────────────────────────────
  let _activeRequestCount = 0;

  function _dbg(...args) {
    // Central debug logger — all lifecycle logs funnel through here
    console.debug("[AGY-DBG]", new Date().toISOString(), ...args);
  }
  // ─────────────────────────────────────────────────────────────────────────

  function generateUUID() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return "req-" + Date.now() + "-" + Math.random().toString(36).substring(2, 11);
  }

  // --------------------------------------------------------------------------
  // DOM Elements
  // --------------------------------------------------------------------------
  const dom = {
    sidebar: document.getElementById("sidebar"),
    toggleSidebarBtn: document.getElementById("toggle-sidebar-btn"),
    closeSidebarBtn: document.getElementById("close-sidebar-btn"),
    newConvBtn: document.getElementById("new-conv-btn"),
    convList: document.getElementById("conversation-list"),
    activeTitle: document.getElementById("active-title"),
    sessionBadge: document.getElementById("session-badge"),
    sessionIdVal: document.getElementById("active-session-id"),
    togglePanelBtn: document.getElementById("toggle-panel-btn"),
    closePanelBtn: document.getElementById("close-panel-btn"),
    mobileBackdrop: document.getElementById("mobile-backdrop"),
    messagesContainer: document.getElementById("messages-container"),
    welcomeHero: document.getElementById("welcome-hero"),
    progressBanner: document.getElementById("progress-banner"),
    progressMsg: document.getElementById("progress-msg"),
    progressSub: document.getElementById("progress-sub"),
    chatForm: document.getElementById("chat-form"),
    chatInput: document.getElementById("chat-input"),
    sendBtn: document.getElementById("send-btn"),
    rightPanel: document.getElementById("right-panel"),
    panelTabs: document.querySelectorAll(".panel-tab"),
    tabPanes: document.querySelectorAll(".tab-pane"),
    traceIntentBadge: document.getElementById("trace-intent-badge"),
    traceLatencyVal: document.getElementById("trace-latency-val"),
    executionGraph: document.getElementById("execution-graph"),
    toolCallsList: document.getElementById("tool-calls-list"),
    chartContainer: document.getElementById("chart-container"),
    chartEmptyState: document.getElementById("chart-empty-state"),
    chartActiveCard: document.getElementById("chart-active-card"),
    chartTitle: document.getElementById("chart-title"),
    chartUnit: document.getElementById("chart-unit"),
    chartSourceVal: document.getElementById("chart-source-val"),
    chartCanvas: document.getElementById("scientific-chart-canvas"),
    chartAvailBadge: document.getElementById("chart-avail-badge"),
    btnDemoChart: document.getElementById("btn-demo-chart"),
    sourcesList: document.getElementById("sources-list"),
  };

  // Configure marked.js for clean sanitized rendering
  if (window.marked) {
    marked.setOptions({
      gfm: true,
      breaks: true,
      headerIds: false,
      mangle: false,
    });
  }

  // --------------------------------------------------------------------------
  // Initialization
  // --------------------------------------------------------------------------
  async function init() {
    setupEventListeners();
    await loadConversations();

    // If no existing conversation, create one
    if (state.conversations.length === 0) {
      await createNewConversation();
    } else {
      await selectConversation(state.conversations[0].session_id);
    }
  }

  // --------------------------------------------------------------------------
  // Event Listeners
  // --------------------------------------------------------------------------
  function setupEventListeners() {
    // New conversation button
    dom.newConvBtn.addEventListener("click", async () => {
      if (state.isLoading) return;
      await createNewConversation();
    });

    // Chat input form
    dom.chatForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const message = dom.chatInput.value.trim();
      if (!message || state.isLoading || dom.sendBtn.disabled) return;
      await handleSendMessage(message);
    });

    // Enter key handling (Enter sends, Shift+Enter new line)
    dom.chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (state.isLoading || dom.sendBtn.disabled) {
          // Do not send while agent is actively generating; preserve user input intact
          return;
        }
        dom.chatForm.dispatchEvent(new Event("submit"));
      }
    });

    // Auto-resize textarea
    dom.chatInput.addEventListener("input", () => {
      dom.chatInput.style.height = "auto";
      dom.chatInput.style.height = Math.min(dom.chatInput.scrollHeight, 140) + "px";
    });

    // Quick suggestion cards
    document.querySelectorAll(".suggestion-card").forEach((card) => {
      card.addEventListener("click", () => {
        const query = card.getAttribute("data-query");
        if (query && !state.isLoading && !dom.sendBtn.disabled) {
          dom.chatInput.value = query;
          dom.chatForm.dispatchEvent(new Event("submit"));
        }
      });
    });

    // Demo chart button in empty chart tab
    if (dom.btnDemoChart) {
      dom.btnDemoChart.addEventListener("click", () => {
        if (state.isLoading || dom.sendBtn.disabled) return;
        const query = "What does global research show about soil pH and bacterial richness? Chart the proportion of bacterial genera by pH tolerance.";
        dom.chatInput.value = query;
        dom.chatForm.dispatchEvent(new Event("submit"));
      });
    }

    // Tab switching in Right Panel
    dom.panelTabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const targetTabId = tab.getAttribute("data-tab");
        switchTab(targetTabId);
      });
    });

    // Toggle Left Sidebar (Desktop collapse & Mobile off-canvas drawer)
    if (dom.toggleSidebarBtn) {
      dom.toggleSidebarBtn.addEventListener("click", () => {
        toggleLeftSidebar();
      });
    }

    if (dom.closeSidebarBtn) {
      dom.closeSidebarBtn.addEventListener("click", () => {
        closeLeftSidebar();
      });
    }

    // Toggle Right Trace Panel (Desktop collapse & Mobile off-canvas drawer)
    if (dom.togglePanelBtn) {
      dom.togglePanelBtn.addEventListener("click", () => {
        toggleRightPanel();
      });
    }

    if (dom.closePanelBtn) {
      dom.closePanelBtn.addEventListener("click", () => {
        closeRightPanel();
      });
    }

    // Mobile Backdrop click closes all open drawers
    if (dom.mobileBackdrop) {
      dom.mobileBackdrop.addEventListener("click", () => {
        closeAllMobileDrawers();
      });
    }

    // Window resize handling: clean up mobile open classes if user resizes to desktop
    window.addEventListener("resize", () => {
      if (!isMobile()) {
        if (dom.sidebar.classList.contains("mobile-open")) {
          dom.sidebar.classList.remove("mobile-open");
        }
        if (dom.rightPanel.classList.contains("mobile-open")) {
          dom.rightPanel.classList.remove("mobile-open");
        }
        if (dom.mobileBackdrop) {
          dom.mobileBackdrop.classList.remove("active");
        }
      }
    });

    // Click to copy session ID
    dom.sessionBadge.addEventListener("click", () => {
      if (!state.activeSessionId) return;
      navigator.clipboard.writeText(state.activeSessionId);
      const originalText = dom.sessionIdVal.textContent;
      dom.sessionIdVal.textContent = "Copied to Clipboard!";
      setTimeout(() => {
        dom.sessionIdVal.textContent = originalText;
      }, 1500);
    });
  }

  // --------------------------------------------------------------------------
  // Sidebar & Panel Collapsible Drawer Controllers
  // --------------------------------------------------------------------------
  function isMobile() {
    return window.innerWidth <= 1024;
  }

  function toggleLeftSidebar() {
    if (isMobile()) {
      const isOpen = dom.sidebar.classList.toggle("mobile-open");
      if (dom.rightPanel.classList.contains("mobile-open")) {
        dom.rightPanel.classList.remove("mobile-open");
      }
      if (dom.mobileBackdrop) {
        dom.mobileBackdrop.classList.toggle("active", isOpen);
      }
    } else {
      const isCollapsed = dom.sidebar.classList.toggle("collapsed");
      if (dom.toggleSidebarBtn) {
        dom.toggleSidebarBtn.classList.toggle("active", !isCollapsed);
        dom.toggleSidebarBtn.setAttribute("title", isCollapsed ? "Open Investigation History" : "Collapse Investigation History");
      }
    }
  }

  function closeLeftSidebar() {
    if (isMobile()) {
      dom.sidebar.classList.remove("mobile-open");
      if (dom.mobileBackdrop && !dom.rightPanel.classList.contains("mobile-open")) {
        dom.mobileBackdrop.classList.remove("active");
      }
    } else {
      dom.sidebar.classList.add("collapsed");
      if (dom.toggleSidebarBtn) {
        dom.toggleSidebarBtn.classList.remove("active");
        dom.toggleSidebarBtn.setAttribute("title", "Open Investigation History");
      }
    }
  }

  function toggleRightPanel() {
    if (isMobile()) {
      const isOpen = dom.rightPanel.classList.toggle("mobile-open");
      if (dom.sidebar.classList.contains("mobile-open")) {
        dom.sidebar.classList.remove("mobile-open");
      }
      if (dom.mobileBackdrop) {
        dom.mobileBackdrop.classList.toggle("active", isOpen);
      }
    } else {
      const isCollapsed = dom.rightPanel.classList.toggle("collapsed");
      if (dom.togglePanelBtn) {
        dom.togglePanelBtn.classList.toggle("active", !isCollapsed);
        dom.togglePanelBtn.setAttribute("title", isCollapsed ? "Open Trace & Chart Panel" : "Collapse Trace & Chart Panel");
      }
    }
  }

  function closeRightPanel() {
    if (isMobile()) {
      dom.rightPanel.classList.remove("mobile-open");
      if (dom.mobileBackdrop && !dom.sidebar.classList.contains("mobile-open")) {
        dom.mobileBackdrop.classList.remove("active");
      }
    } else {
      dom.rightPanel.classList.add("collapsed");
      if (dom.togglePanelBtn) {
        dom.togglePanelBtn.classList.remove("active");
        dom.togglePanelBtn.setAttribute("title", "Open Trace & Chart Panel");
      }
    }
  }

  function openRightPanel() {
    if (isMobile()) {
      dom.rightPanel.classList.add("mobile-open");
      dom.sidebar.classList.remove("mobile-open");
      if (dom.mobileBackdrop) {
        dom.mobileBackdrop.classList.add("active");
      }
    } else {
      dom.rightPanel.classList.remove("collapsed");
      if (dom.togglePanelBtn) {
        dom.togglePanelBtn.classList.add("active");
        dom.togglePanelBtn.setAttribute("title", "Collapse Trace & Chart Panel");
      }
    }
  }

  function closeAllMobileDrawers() {
    dom.sidebar.classList.remove("mobile-open");
    dom.rightPanel.classList.remove("mobile-open");
    if (dom.mobileBackdrop) {
      dom.mobileBackdrop.classList.remove("active");
    }
  }

  function switchTab(tabId) {
    dom.panelTabs.forEach((t) => {
      if (t.getAttribute("data-tab") === tabId) {
        t.classList.add("active");
      } else {
        t.classList.remove("active");
      }
    });
    dom.tabPanes.forEach((pane) => {
      if (pane.id === tabId) {
        pane.classList.add("active");
      } else {
        pane.classList.remove("active");
      }
    });
  }

  // --------------------------------------------------------------------------
  // API Calls: Conversations
  // --------------------------------------------------------------------------
  async function loadConversations() {
    try {
      const resp = await fetch("/api/conversations");
      if (!resp.ok) throw new Error("Failed to load conversations");
      state.conversations = await resp.json();
      renderConversationList();
    } catch (err) {
      console.error("Error loading conversations:", err);
    }
  }

  async function createNewConversation() {
    try {
      showProgress(true, "Initializing Session...", "Creating fresh ADK session");
      const resp = await fetch("/api/conversations", { method: "POST" });
      if (!resp.ok) throw new Error("Failed to create conversation");
      const data = await resp.json();
      state.activeSessionId = data.session_id;
      await loadConversations();
      await selectConversation(data.session_id);
    } catch (err) {
      console.error("Error creating conversation:", err);
      alert("Error creating new conversation: " + err.message);
    } finally {
      showProgress(false);
    }
  }

  async function selectConversation(sessionId) {
    state.activeSessionId = sessionId;
    renderConversationList();

    // Auto-close left drawer on mobile upon selection
    if (isMobile()) {
      closeLeftSidebar();
    }

    // Update Header
    const conv = state.conversations.find((c) => c.session_id === sessionId);
    dom.activeTitle.textContent = conv ? conv.title : "Environmental Investigation";
    dom.sessionIdVal.textContent = sessionId.length > 18 ? sessionId.slice(0, 8) + "..." + sessionId.slice(-6) : sessionId;

    try {
      const resp = await fetch(`/api/conversations/${sessionId}`);
      if (!resp.ok) throw new Error("Failed to fetch conversation details");
      const fullConv = await resp.json();
      renderConversationMessages(fullConv.messages || []);
      
      // If messages exist, restore latest trace and chart
      const lastAssistantMsg = [...(fullConv.messages || [])].reverse().find((m) => m.role === "assistant");
      if (lastAssistantMsg) {
        renderTraceAndGraph(lastAssistantMsg.intent, lastAssistantMsg.tool_trace, lastAssistantMsg.timestamp);
        renderSourcesTab(lastAssistantMsg.sources);
        if (lastAssistantMsg.chart) {
          renderChart(lastAssistantMsg.chart);
        } else {
          clearChart();
        }
      } else {
        resetTraceView();
        clearChart();
      }
    } catch (err) {
      console.error("Error selecting conversation:", err);
    }
  }

  function renderConversationList() {
    dom.convList.innerHTML = "";
    state.conversations.forEach((c) => {
      const item = document.createElement("div");
      item.className = `conv-item ${c.session_id === state.activeSessionId ? "active" : ""}`;
      
      const timeStr = formatTimeAgo(c.updated_at || c.created_at);
      const msgCount = c.message_count || 0;

      item.innerHTML = `
        <div class="conv-item-top">
          <span class="conv-title">${escapeHtml(c.title)}</span>
          ${msgCount > 0 ? `<span class="conv-badge">${msgCount}</span>` : ""}
        </div>
        <div class="conv-time">${timeStr}</div>
      `;

      item.addEventListener("click", () => {
        if (state.activeSessionId !== c.session_id && !state.isLoading) {
          selectConversation(c.session_id);
        }
      });

      dom.convList.appendChild(item);
    });
  }

  // --------------------------------------------------------------------------
  // Message Handling & Execution
  // --------------------------------------------------------------------------
  async function handleSendMessage(messageText) {
    if (state.isLoading || dom.sendBtn.disabled) return;

    state.isLoading = true;
    dom.sendBtn.disabled = true;
    dom.sendBtn.title = "Generating response...";
    dom.chatInput.value = "";
    dom.chatInput.style.height = "auto";

    // Hide welcome hero if visible
    if (dom.welcomeHero) {
      dom.welcomeHero.style.display = "none";
    }

    const requestId = generateUUID();
    const sessionId = state.activeSessionId;
    const userMessageId = "msg-user-" + generateUUID();
    const _sendStart = Date.now();

    // ── [FRONTEND][SEND][START] ───────────────────────────────────────────────
    _activeRequestCount++;
    _dbg("[FRONTEND][SEND][START]",
      `request_id=${requestId}`,
      `session_id=${sessionId}`,
      `message_length=${messageText.length}`,
      `timestamp=${new Date().toISOString()}`
    );
    _dbg("[CONCURRENCY][START]",
      `request_id=${requestId}`,
      `active_requests=${_activeRequestCount}`
    );
    _dbg("[FRONTEND][SEND][PAYLOAD]",
      `request_id=${requestId}`,
      `session_id=${sessionId}`,
      `endpoint=/api/conversations/${sessionId}/message?stream=true`,
      `payload_keys=message,session_id,request_id,stream`
    );
    // ─────────────────────────────────────────────────────────────────────────

    // 1. Render user message immediately with stable correlation IDs
    appendMessage({
      message_id: userMessageId,
      request_id: requestId,
      role: "user",
      text: messageText,
      timestamp: new Date().toISOString(),
    });

    // 2. IMMEDIATELY create the assistant placeholder with matching request_id
    const assistantController = createAssistantPlaceholder(requestId, sessionId);
    activeRequests.set(requestId, assistantController);

    // ── [FRONTEND][ASSISTANT_PLACEHOLDER][CREATED] ───────────────────────────
    _dbg("[FRONTEND][ASSISTANT_PLACEHOLDER][CREATED]",
      `request_id=${requestId}`,
      `message_id=${assistantController.messageId}`,
      `DOM_ID=${assistantController.element ? assistantController.element.getAttribute("data-message-id") : "null"}`,
      `session_id=${sessionId}`,
      `timestamp=${new Date().toISOString()}`
    );
    // ─────────────────────────────────────────────────────────────────────────

    // Initial progress indication based on query keywords
    const lower = messageText.toLowerCase();
    let initialProgress = "Analyzing environmental query...";
    let initialSub = "Orchestrating ADK reasoning engine";
    if (lower.includes("policy") || lower.includes("tavily") || lower.includes("recent")) {
      initialProgress = "Searching external environmental sources...";
      initialSub = "Querying Tavily MCP Search";
    } else if (lower.includes("research") || lower.includes("study") || lower.includes("meta-analysis")) {
      initialProgress = "Searching peer-reviewed research evidence...";
      initialSub = "Querying BigQuery research tables";
    } else if (lower.includes("what is") || lower.includes("define")) {
      initialProgress = "Searching foundational textbook knowledge...";
      initialSub = "Querying BigQuery book tables";
    }

    showProgress(true, initialProgress, initialSub);

    // Per-request diagnostic counters
    let _sseEventCount = 0;
    let _tokenChunkCount = 0;
    let _totalCharsReceived = 0;
    let _firstADKEventMs = null;
    let _firstTextMs = null;
    let _firstBrowserEventMs = null;
    let _streamStatus = "PENDING";

    try {
      // ── [FRONTEND][HTTP][REQUEST_START] ────────────────────────────────────
      _dbg("[FRONTEND][HTTP][REQUEST_START]",
        `request_id=${requestId}`,
        `method=POST`,
        `endpoint=/api/conversations/${sessionId}/message?stream=true`,
        `timestamp=${new Date().toISOString()}`
      );
      // ───────────────────────────────────────────────────────────────────────

      const resp = await fetch(`/api/conversations/${sessionId}/message?stream=true`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream",
        },
        body: JSON.stringify({
          message: messageText,
          session_id: sessionId,
          request_id: requestId,
          stream: true,
        }),
      });

      const contentType = resp.headers.get("content-type") || "";
      const sseExpected = true;
      const sseReceived = contentType.includes("text/event-stream");

      // ── [FRONTEND][HTTP][RESPONSE_HEADERS] ──────────────────────────────────
      _dbg("[FRONTEND][HTTP][RESPONSE_HEADERS]",
        `request_id=${requestId}`,
        `status=${resp.status}`,
        `content_type="${contentType}"`,
        `SSE_EXPECTED=${sseExpected}`,
        `SSE_RECEIVED=${sseReceived}`,
        `timestamp=${new Date().toISOString()}`
      );
      // ───────────────────────────────────────────────────────────────────────

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail || `Server error: ${resp.status}`);
      }

      // Fallback for non-streaming JSON responses
      if (contentType.includes("application/json")) {
        _dbg("[FRONTEND][HTTP][FALLBACK_JSON]",
          `request_id=${requestId}`,
          `note=Response_was_JSON_not_SSE`
        );
        const result = await resp.json();
        assistantController.finalize(result.answer, result.tool_trace);

        // ── [FRONTEND][MESSAGE][FINAL] (JSON path) ─────────────────────────
        _dbg("[FRONTEND][MESSAGE][FINAL]",
          `request_id=${requestId}`,
          `final_length=${(result.answer || "").length}`,
          `path=JSON_fallback`,
          `timestamp=${new Date().toISOString()}`
        );
        // ───────────────────────────────────────────────────────────────────
        if (sessionId === state.activeSessionId) {
          renderTraceAndGraph(result.intent, result.tool_trace, result.total_latency_sec);
          renderSourcesTab(result.sources);
          if (result.chart) {
            renderChart(result.chart);
            switchTab("chart-tab");
          } else {
            clearChart();
          }
        }
        await loadConversations();
        activeRequests.delete(requestId);
        _streamStatus = "SUCCESS_JSON";
        return;
      }

      // ── Stream Server-Sent Events (SSE) ─────────────────────────────────────
      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      // ── [FRONTEND][STREAM][OPEN] ─────────────────────────────────────────
      _dbg("[FRONTEND][STREAM][OPEN]",
        `request_id=${requestId}`,
        `timestamp=${new Date().toISOString()}`
      );
      _dbg("[FRONTEND][STREAM][READ_START]",
        `request_id=${requestId}`,
        `timestamp=${new Date().toISOString()}`
      );
      // ─────────────────────────────────────────────────────────────────────

      while (true) {
        let readResult;
        try {
          readResult = await reader.read();
        } catch (readErr) {
          _dbg("[FRONTEND][STREAM][CLOSED_UNEXPECTEDLY]",
            `request_id=${requestId}`,
            `error=${readErr.message}`,
            `timestamp=${new Date().toISOString()}`
          );
          throw readErr;
        }

        const { value, done } = readResult;
        if (done) {
          // ── [FRONTEND][STREAM][DONE] ─────────────────────────────────────
          _dbg("[FRONTEND][STREAM][DONE]",
            `request_id=${requestId}`,
            `total_events=${_sseEventCount}`,
            `total_chunks=${_tokenChunkCount}`,
            `total_characters=${_totalCharsReceived}`,
            `timestamp=${new Date().toISOString()}`
          );
          // ─────────────────────────────────────────────────────────────────
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop(); // Keep unfinished segment in buffer

        for (const block of blocks) {
          const lines = block.split("\n");
          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed || !trimmed.startsWith("data:")) continue;

            const dataStr = trimmed.slice(5).trim();
            if (!dataStr) continue;

            let eventData;
            try {
              eventData = JSON.parse(dataStr);
            } catch (e) {
              _dbg("[FRONTEND][STREAM][ERROR]",
                `request_id=${requestId}`,
                `error_type=JSON_PARSE_ERROR`,
                `error_message=${e.message}`,
                `raw_preview=${dataStr.slice(0, 80)}`,
                `timestamp=${new Date().toISOString()}`
              );
              console.warn("Could not parse SSE JSON:", dataStr);
              continue;
            }

            _sseEventCount++;
            if (_firstBrowserEventMs === null) {
              _firstBrowserEventMs = Date.now() - _sendStart;
            }

            const evtRequestId = eventData.request_id;
            const evtType = eventData.type || "unknown";

            // ── [FRONTEND][STREAM][EVENT] ──────────────────────────────────
            _dbg("[FRONTEND][STREAM][EVENT]",
              `request_id=${requestId}`,
              `event_type=${evtType}`,
              `sequence_number=${_sseEventCount}`,
              `chunk_length=${dataStr.length}`,
              `incoming_request_id=${evtRequestId || "MISSING"}`,
              `timestamp=${new Date().toISOString()}`
            );
            // ─────────────────────────────────────────────────────────────

            // ── [FRONTEND][ROUTER] ─────────────────────────────────────────
            const targetExists = !!evtRequestId && activeRequests.has(evtRequestId);
            const targetMatchesSend = evtRequestId === requestId;
            _dbg("[FRONTEND][ROUTER]",
              `request_id=${requestId}`,
              `incoming_request_id=${evtRequestId || "MISSING"}`,
              `target_message_id=${targetExists ? activeRequests.get(evtRequestId).messageId : "N/A"}`,
              `target_exists=${targetExists}`,
              `target_matches_this_send=${targetMatchesSend}`,
              `timestamp=${new Date().toISOString()}`
            );
            if (!targetExists) {
              _dbg("[FRONTEND][ROUTER][ERROR]",
                `No assistant target for request_id=${evtRequestId || "MISSING"}`,
                `This event will be DROPPED`,
                `active_request_ids=${JSON.stringify([...activeRequests.keys()])}`
              );
            } else if (!targetMatchesSend) {
              _dbg("[FRONTEND][ROUTER][MISMATCH]",
                `incoming_request_id=${evtRequestId}`,
                `this_send_request_id=${requestId}`,
                `WARNING=Event routed to DIFFERENT send call`
              );
            }
            // ─────────────────────────────────────────────────────────────

            // Ignore events that are missing request_id or do not match an active request
            if (!evtRequestId || !activeRequests.has(evtRequestId)) {
              continue;
            }

            const targetController = activeRequests.get(evtRequestId);

            // 1. START EVENT
            if (eventData.type === "start") {
              if (_firstADKEventMs === null) _firstADKEventMs = Date.now() - _sendStart;
              targetController.updateStatus("Reasoning...");
            }

            // 2. TOOL EVENT (Running)
            else if (eventData.type === "tool" || eventData.type === "tool_start") {
              const toolName = formatToolShortLabel(eventData.tool_name || eventData.name);
              targetController.updateStatus(`Querying ${toolName}...`);
              if (targetController.sessionId === state.activeSessionId) {
                showProgress(
                  true,
                  `Executing ${toolName}...`,
                  eventData.is_parallel ? "Parallel Tool Execution (Concurrent)" : "ADK Tool Call"
                );
              }
            }

            // 3. TOOL COMPLETE EVENT
            else if (eventData.type === "tool_complete") {
              const tool = eventData.tool;
              targetController.accumulatedToolTraces.push(tool);
              const toolName = formatToolShortLabel(tool.tool_name || tool.name);
              targetController.updateStatus(`Retrieved from ${toolName}`);

              if (targetController.sessionId === state.activeSessionId) {
                const tablesInfo = (tool.tables && tool.tables.length > 0)
                  ? `Tables: ${tool.tables.join(", ")}`
                  : "Retrieved evidence";
                showProgress(
                  true,
                  `Completed ${toolName} (${tool.duration_ms || 0}ms)`,
                  tablesInfo
                );
                renderTraceAndGraph("Scientific Reasoning", targetController.accumulatedToolTraces, 0);
                if (tool.detailed_sources && tool.detailed_sources.length > 0) {
                  renderSourcesTab(tool.detailed_sources);
                }
              }
            }

            // 4. STATUS EVENT
            else if (eventData.type === "status") {
              targetController.updateStatus(eventData.message || "Generating response...");
              if (targetController.sessionId === state.activeSessionId) {
                showProgress(
                  true,
                  eventData.message || "Synthesizing grounded response...",
                  "Gemini 3.5 Flash Streaming"
                );
              }
            }

            // 5. TEXT / TOKEN EVENT
            else if (eventData.type === "token" || eventData.type === "text_chunk") {
              const tokenText = eventData.content || eventData.delta || "";
              _tokenChunkCount++;
              _totalCharsReceived += tokenText.length;

              if (_firstTextMs === null) {
                _firstTextMs = Date.now() - _sendStart;
                // ── [FRONTEND][MESSAGE][FIRST_CONTENT] ──────────────────
                _dbg("[FRONTEND][MESSAGE][FIRST_CONTENT]",
                  `request_id=${requestId}`,
                  `first_chunk_latency_ms=${_firstTextMs}`,
                  `chunk_preview="${tokenText.slice(0, 60)}"`,
                  `timestamp=${new Date().toISOString()}`
                );
                // ─────────────────────────────────────────────────────────
              }

              const prevLen = targetController._accumulatedLength || 0;
              // ── [FRONTEND][MESSAGE][APPEND] ────────────────────────────
              _dbg("[FRONTEND][MESSAGE][APPEND]",
                `request_id=${requestId}`,
                `message_id=${targetController.messageId}`,
                `chunk_number=${_tokenChunkCount}`,
                `chunk_length=${tokenText.length}`,
                `timestamp=${new Date().toISOString()}`
              );
              // ─────────────────────────────────────────────────────────

              if (targetController.sessionId === state.activeSessionId) {
                showProgress(false);
              }
              targetController.appendChunk(tokenText);
            }

            // 6. DONE EVENT
            else if (eventData.type === "done") {
              const finalText = eventData.answer || eventData.content || "";
              // ── [FRONTEND][MESSAGE][FINAL] ─────────────────────────────
              _dbg("[FRONTEND][MESSAGE][FINAL]",
                `request_id=${requestId}`,
                `message_id=${targetController.messageId}`,
                `final_length=${finalText.length}`,
                `streamed_chars=${_totalCharsReceived}`,
                `timestamp=${new Date().toISOString()}`
              );
              // ─────────────────────────────────────────────────────────

              targetController.finalize(finalText, eventData.tool_trace);

              if (targetController.sessionId === state.activeSessionId) {
                renderTraceAndGraph(eventData.intent, eventData.tool_trace, eventData.total_latency_sec);
                renderSourcesTab(eventData.sources);
                if (eventData.chart) {
                  renderChart(eventData.chart);
                  switchTab("chart-tab");
                } else {
                  clearChart();
                }
              }

              activeRequests.delete(evtRequestId);
              _streamStatus = "SUCCESS";

              // ── [REQUEST_SUMMARY] ──────────────────────────────────────
              const _totalMs = Date.now() - _sendStart;
              _dbg("[REQUEST_SUMMARY]",
                `request_id=${requestId}`,
                `session_id=${sessionId}`,
                `tool_calls=${(eventData.tool_trace || []).length}`,
                `first_adk_event_ms=${_firstADKEventMs}`,
                `first_text_ms=${_firstTextMs}`,
                `first_browser_event_ms=${_firstBrowserEventMs}`,
                `total_duration_ms=${_totalMs}`,
                `response_chars=${finalText.length}`,
                `stream_events=${_sseEventCount}`,
                `status=SUCCESS`
              );
              // ─────────────────────────────────────────────────────────

              // Update conversation list in sidebar
              await loadConversations();
              const currentConv = state.conversations.find((c) => c.session_id === state.activeSessionId);
              if (currentConv) {
                dom.activeTitle.textContent = currentConv.title;
              }
            }

            // 7. ERROR EVENT
            else if (eventData.type === "error") {
              _dbg("[FRONTEND][STREAM][ERROR]",
                `request_id=${requestId}`,
                `error_type=SERVER_ERROR_EVENT`,
                `error_message=${eventData.message || "unknown"}`,
                `timestamp=${new Date().toISOString()}`
              );
              targetController.fail(eventData.message || "Streaming error encountered");
              activeRequests.delete(evtRequestId);
              _streamStatus = "SERVER_ERROR";
            }
          }
        }
      }
    } catch (err) {
      // ── [FRONTEND][STREAM][ERROR] ──────────────────────────────────────────
      _dbg("[FRONTEND][STREAM][ERROR]",
        `request_id=${requestId}`,
        `error_type=${err.name || "Error"}`,
        `error_message=${err.message}`,
        `timestamp=${new Date().toISOString()}`
      );
      _streamStatus = "CLIENT_EXCEPTION";
      // ─────────────────────────────────────────────────────────────────────

      console.error("Error processing query:", err);
      if (activeRequests.has(requestId)) {
        activeRequests.get(requestId).fail(err.message);
        activeRequests.delete(requestId);
      } else {
        appendErrorMessage(err.message);
      }
    } finally {
      _activeRequestCount = Math.max(0, _activeRequestCount - 1);
      // ── [CONCURRENCY][END] ─────────────────────────────────────────────────
      _dbg("[CONCURRENCY][END]",
        `request_id=${requestId}`,
        `active_requests=${_activeRequestCount}`,
        `stream_status=${_streamStatus}`,
        `timestamp=${new Date().toISOString()}`
      );
      // ─────────────────────────────────────────────────────────────────────

      showProgress(false);
      state.isLoading = false;
      dom.sendBtn.disabled = false;
      dom.sendBtn.title = "Send message";
      dom.chatInput.focus();
      scrollToBottom();
    }
  }

  // --------------------------------------------------------------------------
  // Chat Rendering
  // --------------------------------------------------------------------------

  function renderConversationMessages(messages) {
    dom.messagesContainer.innerHTML = "";

    if (!messages || messages.length === 0) {
      if (dom.welcomeHero) {
        dom.messagesContainer.appendChild(dom.welcomeHero);
        dom.welcomeHero.style.display = "flex";
      }
      return;
    }

    if (dom.welcomeHero) {
      dom.welcomeHero.style.display = "none";
    }

    messages.forEach((msg) => {
      appendMessage(msg);
    });

    scrollToBottom();
  }

  function createAssistantPlaceholder(requestId, sessionId) {
    const msgId = "msg-asst-" + generateUUID();
    const msgEl = document.createElement("div");
    msgEl.className = "chat-message message-assistant";
    msgEl.setAttribute("data-request-id", requestId);
    msgEl.setAttribute("data-message-id", msgId);
    msgEl.setAttribute("data-session-id", sessionId);

    msgEl.innerHTML = `
      <div class="avatar avatar-assistant">🔬</div>
      <div class="message-bubble">
        <div class="message-meta">
          <span class="sender-name">Environmental AI Scientist</span>
          <span class="timestamp">${formatTimeString(new Date().toISOString())}</span>
          <span class="bubble-status-badge" style="font-size: 11px; color: var(--color-accent-blue); margin-left: 8px; opacity: 0.85;">
            Reasoning...
          </span>
        </div>
        <div class="markdown-body">
          <span class="streaming-cursor">▊</span>
        </div>
        <div class="msg-actions" style="display: none;"></div>
      </div>
    `;

    dom.messagesContainer.appendChild(msgEl);
    scrollToBottom();

    const markdownBody = msgEl.querySelector(".markdown-body");
    const actionsEl = msgEl.querySelector(".msg-actions");
    const statusBadge = msgEl.querySelector(".bubble-status-badge");
    let accumulated = "";
    let isFinalized = false;

    const controller = {
      requestId,
      sessionId,
      messageId: msgId,
      element: msgEl,
      accumulatedToolTraces: [],
      status: "streaming",

      updateStatus(statusText) {
        if (isFinalized || !statusBadge) return;
        statusBadge.textContent = statusText;
        statusBadge.style.display = "inline";
      },

      appendChunk(chunk) {
        if (isFinalized) return;
        if (statusBadge) {
          statusBadge.style.display = "none";
        }
        accumulated += chunk;
        markdownBody.innerHTML = formatAssistantMarkdown(accumulated) + '<span class="streaming-cursor">▊</span>';
        scrollToBottom();
      },

      finalize(finalText, toolTrace) {
        if (isFinalized) return;
        isFinalized = true;
        controller.status = "complete";
        if (statusBadge) {
          statusBadge.remove();
        }
        accumulated = finalText || accumulated;
        markdownBody.innerHTML = formatAssistantMarkdown(accumulated);

        if (toolTrace && toolTrace.length > 0) {
          actionsEl.style.display = "flex";
          actionsEl.innerHTML = `
            <button class="btn-view-trace" title="Inspect tool calls & tables">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
              </svg>
              View Grounding Trace (${toolTrace.length} tool${toolTrace.length > 1 ? "s" : ""})
            </button>
          `;
          const traceBtn = actionsEl.querySelector(".btn-view-trace");
          if (traceBtn) {
            traceBtn.addEventListener("click", () => {
              openRightPanel();
              switchTab("graph-tab");
            });
          }
        }
        scrollToBottom();
      },

      fail(errorMsg) {
        if (isFinalized) return;
        isFinalized = true;
        controller.status = "failed";
        if (statusBadge) {
          statusBadge.textContent = "Failed";
          statusBadge.style.color = "var(--color-danger, #ef4444)";
        }
        const cursor = markdownBody.querySelector(".streaming-cursor");
        if (cursor) cursor.remove();
        if (!accumulated) {
          markdownBody.innerHTML = `<div class="error-msg-bubble">⚠️ ${escapeHtml(errorMsg)}</div>`;
        } else {
          markdownBody.innerHTML += `<div class="error-msg-bubble" style="margin-top: 8px;">⚠️ ${escapeHtml(errorMsg)}</div>`;
        }
        scrollToBottom();
      }
    };

    return controller;
  }

  function appendMessage(msg) {
    const isUser = msg.role === "user";
    const msgEl = document.createElement("div");
    msgEl.className = `chat-message ${isUser ? "message-user" : "message-assistant"}`;
    if (msg.request_id) {
      msgEl.setAttribute("data-request-id", msg.request_id);
    }
    if (msg.message_id) {
      msgEl.setAttribute("data-message-id", msg.message_id);
    }

    const avatarHtml = isUser
      ? `<div class="avatar avatar-user">U</div>`
      : `<div class="avatar avatar-assistant">🔬</div>`;

    const timeStr = formatTimeString(msg.timestamp);

    let contentHtml = "";
    const textContent = msg.text || msg.content || "";
    if (isUser) {
      contentHtml = `<p>${escapeHtml(textContent)}</p>`;
    } else {
      contentHtml = formatAssistantMarkdown(textContent);
    }

    msgEl.innerHTML = `
      ${avatarHtml}
      <div class="message-bubble">
        <div class="message-meta">
          <span class="sender-name">${isUser ? "Scientist / User" : "Environmental AI Scientist"}</span>
          <span class="timestamp">${timeStr}</span>
        </div>
        <div class="markdown-body">
          ${contentHtml}
        </div>
        ${!isUser && msg.tool_trace && msg.tool_trace.length > 0 ? `
          <div class="msg-actions">
            <button class="btn-view-trace" title="Inspect tool calls & tables">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
              </svg>
              View Grounding Trace (${msg.tool_trace.length} tool${msg.tool_trace.length > 1 ? "s" : ""})
            </button>
          </div>
        ` : ""}
      </div>
    `;

    // Hook up view trace button
    const traceBtn = msgEl.querySelector(".btn-view-trace");
    if (traceBtn) {
      traceBtn.addEventListener("click", () => {
        openRightPanel();
        switchTab("graph-tab");
      });
    }

    dom.messagesContainer.appendChild(msgEl);
    scrollToBottom();
  }

  function appendErrorMessage(errorMsg) {
    const msgEl = document.createElement("div");
    msgEl.className = "chat-message message-assistant";
    msgEl.innerHTML = `
      <div class="avatar" style="background-color: #ef4444; color: #fff;">⚠️</div>
      <div class="message-bubble" style="border-color: #ef4444;">
        <div class="message-meta">
          <span class="sender-name" style="color: #ef4444;">System Error</span>
          <span class="timestamp">${formatTimeString(new Date().toISOString())}</span>
        </div>
        <div class="markdown-body" style="color: #fca5a5;">
          <p><strong>Reasoning interrupted:</strong> ${escapeHtml(errorMsg)}</p>
          <p style="font-size: 12px; color: var(--text-muted); margin-top: 6px;">
            Please verify network connectivity, BigQuery permissions, and try again.
          </p>
        </div>
      </div>
    `;
    dom.messagesContainer.appendChild(msgEl);
  }

  /**
   * Formats structured scientific response markdown into clean visual cards.
   */
  function formatAssistantMarkdown(text) {
    if (!text) return "";

    // Replace Markdown headers with structured container tags
    let formatted = text;

    // Use marked if available
    let renderedHtml = "";
    if (window.marked) {
      renderedHtml = marked.parse(formatted);
    } else {
      // Fallback simple line-by-line formatting
      renderedHtml = escapeHtml(text).replace(/\n/g, "<br>");
    }

    // Enhance structured H3 sections with CSS classes
    renderedHtml = renderedHtml
      .replace(/<h3>Assessment<\/h3>/gi, `<div class="section-assessment"><h3>Assessment</h3></div>`)
      .replace(/<h3>Recommendation<\/h3>/gi, `<div class="section-recommendation"><h3>Recommendation</h3></div>`)
      .replace(/<h3>Impacted Metrics<\/h3>/gi, `<div class="section-metrics"><h3>Impacted Metrics</h3></div>`)
      .replace(/<h3>Evidence<\/h3>/gi, `<div class="section-evidence"><h3>Evidence</h3></div>`)
      .replace(/<h3>Time Horizon<\/h3>/gi, `<div class="section-horizon"><h3>Time Horizon</h3></div>`)
      .replace(/<h3>Limitations<\/h3>/gi, `<div class="section-limitations"><h3>Limitations</h3></div>`)
      .replace(/<h3>Confidence<\/h3>/gi, `<div class="section-confidence"><h3>Confidence</h3></div>`);

    // Enhance Evidence bullets with color-coded provenance borders
    renderedHtml = renderedHtml.replace(/<li><strong>Book:<\/strong>/gi, `<li class="evidence-item evidence-book"><strong>Book:</strong>`);
    renderedHtml = renderedHtml.replace(/<li><strong>Research:<\/strong>/gi, `<li class="evidence-item evidence-research"><strong>Research:</strong>`);
    renderedHtml = renderedHtml.replace(/<li><strong>External:<\/strong>/gi, `<li class="evidence-item evidence-external"><strong>External:</strong>`);

    return renderedHtml;
  }

  function showProgress(visible, message = "", sub = "") {
    if (visible) {
      dom.progressBanner.style.display = "flex";
      dom.progressMsg.textContent = message;
      dom.progressSub.textContent = sub;
    } else {
      dom.progressBanner.style.display = "none";
    }
  }

  function scrollToBottom() {
    dom.messagesContainer.scrollTop = dom.messagesContainer.scrollHeight;
  }

  // --------------------------------------------------------------------------
  // Right Panel: Trace & Graph Rendering
  // --------------------------------------------------------------------------
  function renderTraceAndGraph(intent, toolTraces, latencyVal) {
    dom.traceIntentBadge.textContent = intent || "Scientific Reasoning";
    dom.traceLatencyVal.textContent = latencyVal ? `${latencyVal}s` : "—";

    // 1. Render Execution Flow Graph (Sequential vs Parallel representation)
    renderExecutionGraph(toolTraces);

    // 2. Render Tool Detail Cards
    renderToolCallsList(toolTraces);
  }

  function renderExecutionGraph(toolTraces) {
    dom.executionGraph.innerHTML = "";

    if (!toolTraces || toolTraces.length === 0) {
      dom.executionGraph.innerHTML = `
        <div class="graph-step">
          <div class="graph-node-container">
            <div class="graph-node">
              <div class="node-left">
                <span class="node-type-dot dot-synthesis"></span>
                <span class="node-title">Direct Scientific Synthesis (No Tools Required)</span>
              </div>
              <span class="node-check">✓</span>
            </div>
          </div>
        </div>
      `;
      return;
    }

    // Group tools by step counter (detect parallel execution branches)
    const stepGroups = {};
    toolTraces.forEach((t) => {
      const s = t.step || 1;
      if (!stepGroups[s]) stepGroups[s] = [];
      stepGroups[s].push(t);
    });

    const stepKeys = Object.keys(stepGroups).sort((a, b) => Number(a) - Number(b));

    stepKeys.forEach((stepKey, idx) => {
      const toolsInStep = stepGroups[stepKey];
      const isParallel = toolsInStep.length > 1;

      const stepEl = document.createElement("div");
      stepEl.className = "graph-step";

      let nodesHtml = toolsInStep.map((tool) => {
        let dotClass = "dot-research";
        if (tool.source_type === "BOOK") dotClass = "dot-book";
        else if (tool.source_type === "EXTERNAL") dotClass = "dot-external";

        const label = formatToolShortLabel(tool.name || tool.tool_name);
        const latencyMs = tool.latency_ms || Math.round((tool.latency_sec || 0) * 1000);
        const statusText = tool.status === "failed" ? "✗" : "✓";
        const statusClass = tool.status === "failed" ? "node-fail" : "node-check";

        return `
          <div class="graph-node ${isParallel ? 'graph-node-parallel' : ''}" title="${escapeHtml(tool.name || tool.tool_name)} (${latencyMs}ms)">
            <div class="node-left">
              <span class="node-type-dot ${dotClass}"></span>
              <div class="node-label-group">
                <span class="node-title">${label}</span>
                <span class="node-sub">${latencyMs}ms • ${tool.source_type}</span>
              </div>
            </div>
            <span class="${statusClass}">${statusText}</span>
          </div>
        `;
      }).join("");

      stepEl.innerHTML = `
        ${isParallel ? `<div class="parallel-branch-indicator">⑂ Parallel Tool Execution (${toolsInStep.length} concurrent calls)</div>` : ""}
        <div class="graph-node-container" style="${isParallel ? 'display: flex; gap: 8px; flex-wrap: wrap;' : ''}">
          ${nodesHtml}
        </div>
      `;
      dom.executionGraph.appendChild(stepEl);

      // Add downward connector
      const connectorEl = document.createElement("div");
      connectorEl.className = "graph-connector";
      connectorEl.innerHTML = `<span>↓</span>`;
      dom.executionGraph.appendChild(connectorEl);
    });

    // Final Synthesis Node
    const synthStep = document.createElement("div");
    synthStep.className = "graph-step";
    synthStep.innerHTML = `
      <div class="graph-node-container">
        <div class="graph-node" style="border-color: var(--color-hybrid-border);">
          <div class="node-left">
            <span class="node-type-dot dot-synthesis"></span>
            <div class="node-label-group">
              <span class="node-title">Gemini 3.5 Flash Synthesis</span>
              <span class="node-sub">ADK Grounded Response Delivery</span>
            </div>
          </div>
          <span class="node-check">✓</span>
        </div>
      </div>
    `;
    dom.executionGraph.appendChild(synthStep);
  }

  function formatToolShortLabel(name) {
    if (name === "search_book_knowledge") return "Book Knowledge (BigQuery)";
    if (name === "search_research_evidence") return "Research Evidence (BigQuery)";
    if (name === "get_paper_metadata") return "Paper Metadata (BigQuery)";
    if (name === "tavily_search") return "Tavily Web Search";
    if (name === "tavily_research") return "Tavily Deep Research";
    if (name === "tavily_extract") return "Tavily Page Extract";
    return name || "Unknown Tool";
  }

  function renderToolCallsList(toolTraces) {
    dom.toolCallsList.innerHTML = "";

    if (!toolTraces || toolTraces.length === 0) {
      dom.toolCallsList.innerHTML = `<div class="empty-trace">No tools invoked for this response.</div>`;
      return;
    }

    toolTraces.forEach((tool) => {
      const card = document.createElement("div");
      card.className = "tool-card";

      let badgeClass = "badge-research";
      if (tool.source_type === "BOOK") badgeClass = "badge-book";
      else if (tool.source_type === "EXTERNAL") badgeClass = "badge-external";

      const latencyMs = tool.latency_ms || Math.round((tool.latency_sec || 0) * 1000);
      const simText = tool.top_similarity !== null && tool.top_similarity !== undefined
        ? `Similarity: ${(tool.top_similarity * 100).toFixed(1)}%`
        : `Results: ${tool.result_count}`;

      const tablesHtml = (tool.tables || []).map((tbl) => `<span class="table-tag" title="${escapeHtml(tbl)}">${escapeHtml(tbl)}</span>`).join("");

      const titlesHtml = (tool.source_titles || []).map((t) => `
        <div class="source-item-mini">
          <span class="source-bullet">▸</span>
          <span>${escapeHtml(t)}</span>
        </div>
      `).join("");

      const argsObj = tool.arguments || { query: tool.query };
      const argsFormatted = escapeHtml(JSON.stringify(argsObj, null, 2));

      card.innerHTML = `
        <div class="tool-card-header">
          <div class="tool-badge-row">
            <span class="card-badge ${badgeClass}">${tool.source_type}</span>
            <span class="tool-name-lbl">${escapeHtml(tool.tool_name || tool.name)}</span>
          </div>
          <span class="tool-latency">${latencyMs} ms</span>
        </div>

        <div class="payload-section">
          <span class="payload-label">INPUT ARGUMENTS:</span>
          <pre class="payload-code"><code>${argsFormatted}</code></pre>
        </div>

        <div class="tool-meta-grid">
          <div class="tool-tables-box">
            <span class="tables-lbl">MATCH METRIC</span>
            <span class="table-tag" style="color: #60a5fa;">${simText}</span>
          </div>
          <div class="tool-tables-box">
            <span class="tables-lbl">COUNT RETURNED</span>
            <span class="table-tag">${tool.result_count} units</span>
          </div>
        </div>
        <div class="tool-tables-box">
          <span class="tables-lbl">DATASET TABLES / APIS</span>
          ${tablesHtml}
        </div>
        ${titlesHtml ? `
          <div class="tool-sources-box">
            <span class="tables-lbl">EVIDENCE TITLES RETRIEVED</span>
            ${titlesHtml}
          </div>
        ` : ""}
      `;

      dom.toolCallsList.appendChild(card);
    });
  }

  function renderSourcesTab(sources) {
    dom.sourcesList.innerHTML = "";

    if (!sources || sources.length === 0) {
      dom.sourcesList.innerHTML = `<div class="empty-trace">No external or research citations extracted in this turn.</div>`;
      return;
    }

    sources.forEach((src) => {
      const card = document.createElement("div");
      card.className = "source-card-detailed";

      let badgeClass = "badge-research";
      if (src.type === "BOOK") badgeClass = "badge-book";
      else if (src.type === "EXTERNAL") badgeClass = "badge-external";

      const urlLink = src.url ? `
        <div class="src-card-link">
          <a href="${escapeHtml(src.url)}" target="_blank" rel="noopener noreferrer" class="source-external-link">
            <span>🔗 ${escapeHtml(src.url.length > 50 ? src.url.slice(0, 48) + '...' : src.url)}</span>
          </a>
        </div>
      ` : "";

      const detailHtml = src.detail ? `
        <div class="src-card-detail">
          ${escapeHtml(src.detail)}
        </div>
      ` : "";

      card.innerHTML = `
        <div class="src-card-header">
          <span class="card-badge ${badgeClass}">${src.type}</span>
          <span class="src-card-title">${escapeHtml(src.title)}</span>
        </div>
        ${detailHtml}
        ${urlLink}
        <div class="src-card-tables">
          ${(src.tables || []).join(" • ")}
        </div>
      `;

      dom.sourcesList.appendChild(card);
    });
  }

  function resetTraceView() {
    dom.traceIntentBadge.textContent = "Awaiting Query";
    dom.traceLatencyVal.textContent = "—";
    dom.executionGraph.innerHTML = `<div class="graph-placeholder"><span>Execute a query to inspect live tool orchestration graph.</span></div>`;
    dom.toolCallsList.innerHTML = `<div class="empty-trace">No tools invoked yet.</div>`;
    dom.sourcesList.innerHTML = `<div class="empty-trace">No sources retrieved in current turn.</div>`;
  }

  // --------------------------------------------------------------------------
  // Right Panel: Scientific Chart Rendering (Chart.js)
  // --------------------------------------------------------------------------
  function renderChart(chartSpec) {
    if (!chartSpec || !chartSpec.labels || !chartSpec.values) {
      clearChart();
      return;
    }

    // Automatically reveal right panel and activate chart tab
    openRightPanel();
    switchTab("chart-tab");

    // Display active chart card
    dom.chartEmptyState.style.display = "none";
    dom.chartActiveCard.style.display = "flex";
    dom.chartAvailBadge.style.display = "inline-block";

    // Populate header & metadata
    dom.chartTitle.textContent = chartSpec.title || "Scientific Data Visualization";
    dom.chartUnit.textContent = chartSpec.unit || "";
    dom.chartSourceVal.textContent = chartSpec.source || "Peer-Reviewed Scientific Literature";

    // Destroy prior chart instance if any
    if (state.chartInstance) {
      state.chartInstance.destroy();
      state.chartInstance = null;
    }

    const ctx = dom.chartCanvas.getContext("2d");

    // High-contrast scientific palette (Blue, Indigo, Amber, Sky Blue, Violet, Rose)
    const bgColors = [
      "rgba(59, 130, 246, 0.75)",
      "rgba(99, 102, 241, 0.75)",
      "rgba(245, 158, 11, 0.75)",
      "rgba(14, 165, 233, 0.75)",
      "rgba(168, 85, 247, 0.75)",
      "rgba(236, 72, 153, 0.75)",
    ];
    const borderColors = [
      "#3b82f6",
      "#6366f1",
      "#f59e0b",
      "#0ea5e9",
      "#a855f7",
      "#ec4899",
    ];

    const chartType = chartSpec.type === "line" ? "line" : chartSpec.type === "doughnut" ? "doughnut" : "bar";

    state.chartInstance = new Chart(ctx, {
      type: chartType,
      data: {
        labels: chartSpec.labels,
        datasets: [{
          label: chartSpec.unit || "Value",
          data: chartSpec.values,
          backgroundColor: bgColors.slice(0, chartSpec.values.length),
          borderColor: borderColors.slice(0, chartSpec.values.length),
          borderWidth: 1.5,
          borderRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: chartType === "doughnut",
            labels: { color: "#d1d5db", font: { family: "Inter", size: 11 } },
          },
          tooltip: {
            backgroundColor: "#111319",
            titleColor: "#ffffff",
            bodyColor: "#60a5fa",
            borderColor: "rgba(255, 255, 255, 0.2)",
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: (ctx) => ` ${ctx.dataset.label}: ${ctx.formattedValue} ${chartSpec.unit || ""}`,
            },
          },
        },
        scales: chartType === "doughnut" ? {} : {
          x: {
            grid: { color: "rgba(255, 255, 255, 0.08)" },
            ticks: { color: "#9ca3af", font: { family: "Inter", size: 10.5 } },
          },
          y: {
            grid: { color: "rgba(255, 255, 255, 0.08)" },
            ticks: { color: "#9ca3af", font: { family: "Inter", size: 10.5 } },
          },
        },
      },
    });
  }

  function clearChart() {
    if (state.chartInstance) {
      state.chartInstance.destroy();
      state.chartInstance = null;
    }
    dom.chartEmptyState.style.display = "flex";
    dom.chartActiveCard.style.display = "none";
    dom.chartAvailBadge.style.display = "none";
  }

  // --------------------------------------------------------------------------
  // Utility Helpers
  // --------------------------------------------------------------------------
  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatTimeString(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  }

  function formatTimeAgo(iso) {
    if (!iso) return "just now";
    try {
      const d = new Date(iso);
      const diffMs = Date.now() - d.getTime();
      const diffMins = Math.floor(diffMs / (1000 * 60));
      if (diffMins < 1) return "just now";
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      return d.toLocaleDateString([], { month: "short", day: "numeric" });
    } catch {
      return "";
    }
  }

  // Kickoff initialization
  init();
});
