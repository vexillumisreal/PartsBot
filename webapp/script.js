const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

const State = {
    view: 'catalog', // 'catalog', 'cart', 'success'
    level: 'brands', // 'brands', 'models', 'parts', 'search'
    currentBrand: null,
    currentModel: null,
    currentCategoryChip: 'all',
    searchQuery: '',
    cart: [],
    userStatus: 'retail',
    currentParts: []
};

// DOM Elements
const els = {
    viewCatalog: document.getElementById('view-catalog'),
    viewCart: document.getElementById('view-cart'),
    viewSuccess: document.getElementById('view-success'),
    catalogList: document.getElementById('catalog-list'),
    loading: document.getElementById('loading'),
    btnCart: document.getElementById('btn-cart'),
    cartBadge: document.getElementById('cart-badge'),
    btnBack: document.getElementById('btn-back'),
    headerTitle: document.getElementById('header-title'),
    searchContainer: document.getElementById('search-container'),
    searchInput: document.getElementById('search-input'),
    btnClearSearch: document.getElementById('btn-clear-search'),
    breadcrumbs: document.getElementById('breadcrumbs'),
    categoryChips: document.getElementById('category-chips'),
    deliveryMethod: document.getElementById('delivery_method'),
    deliveryAddressWrapper: document.getElementById('delivery_address_wrapper'),
    btnSuccessCatalog: document.getElementById('btn-success-catalog'),
    btnSuccessClose: document.getElementById('btn-success-close')
};

// Initialize
async function init() {
    setupEventListeners();
    await fetchUserStatus();
    await loadBrands();
}

function setupEventListeners() {
    els.btnCart.addEventListener('click', () => {
        if (State.view === 'catalog') {
            showCart();
        } else {
            showCatalog();
        }
    });

    els.btnBack.addEventListener('click', goBack);

    els.searchInput.addEventListener('input', (e) => {
        State.searchQuery = e.target.value.toLowerCase().trim();
        els.btnClearSearch.style.display = State.searchQuery ? 'block' : 'none';
        
        if (State.searchTimeout) clearTimeout(State.searchTimeout);
        State.searchTimeout = setTimeout(() => {
            if (State.level === 'parts') {
                renderParts(); // local filter
            } else if (State.searchQuery.length >= 2) {
                searchGlobalParts(State.searchQuery);
            } else if (State.searchQuery.length === 0) {
                if (State.level === 'brands') loadBrands();
                else if (State.level === 'models') loadModels(State.currentBrand);
            }
        }, 250);
    });

    els.btnClearSearch.addEventListener('click', () => {
        els.searchInput.value = '';
        State.searchQuery = '';
        els.btnClearSearch.style.display = 'none';
        if (State.level === 'brands') loadBrands();
        else if (State.level === 'models') loadModels(State.currentBrand);
        else if (State.level === 'parts') renderParts();
    });

    els.deliveryMethod.addEventListener('change', (e) => {
        els.deliveryAddressWrapper.style.display = e.target.value === 'pickup' ? 'none' : 'block';
    });

    if (els.btnSuccessCatalog) {
        els.btnSuccessCatalog.addEventListener('click', () => {
            showCatalog();
            loadBrands();
        });
    }

    if (els.btnSuccessClose) {
        els.btnSuccessClose.addEventListener('click', () => {
            tg.close();
        });
    }

    tg.onEvent('mainButtonClicked', submitOrder);
}

// Navigation & Routing
function updateHeader() {
    if (State.view === 'cart') {
        els.headerTitle.textContent = 'Корзина';
        els.btnBack.style.display = 'flex';
        els.btnCart.style.display = 'none';
        els.searchContainer.style.display = 'none';
        els.breadcrumbs.style.display = 'none';
        if (els.categoryChips) els.categoryChips.style.display = 'none';
        tg.MainButton.text = "ОФОРМИТЬ ЗАКАЗ";
        tg.MainButton.show();
    } else if (State.view === 'success') {
        els.headerTitle.textContent = 'Заказ оформлен';
        els.btnBack.style.display = 'none';
        els.btnCart.style.display = 'none';
        els.searchContainer.style.display = 'none';
        els.breadcrumbs.style.display = 'none';
        if (els.categoryChips) els.categoryChips.style.display = 'none';
        tg.MainButton.hide();
    } else {
        els.btnCart.style.display = 'flex';
        els.searchContainer.style.display = 'block';
        updateCartBadge();
        
        if (State.level === 'brands') {
            els.headerTitle.textContent = 'Каталог';
            els.btnBack.style.display = 'none';
            els.breadcrumbs.style.display = 'none';
            if (els.categoryChips) els.categoryChips.style.display = 'none';
        } else {
            els.btnBack.style.display = 'flex';
            els.headerTitle.textContent = State.level === 'models' ? State.currentBrand : State.currentModel;
            renderBreadcrumbs();
            if (State.level === 'parts') {
                renderCategoryChips();
            } else {
                if (els.categoryChips) els.categoryChips.style.display = 'none';
            }
        }
        
        tg.MainButton.hide();
    }
}

function renderBreadcrumbs() {
    els.breadcrumbs.style.display = 'flex';
    let html = `<span class="breadcrumb-item" id="bc-brands">Каталог</span>`;
    
    if (State.currentBrand) {
        html += `<span class="breadcrumb-separator">›</span>`;
        if (State.level === 'models') {
            html += `<span class="breadcrumb-current">${escapeHtml(State.currentBrand)}</span>`;
        } else {
            html += `<span class="breadcrumb-item" id="bc-models">${escapeHtml(State.currentBrand)}</span>`;
        }
    }
    
    if (State.currentModel && State.level === 'parts') {
        html += `<span class="breadcrumb-separator">›</span>`;
        html += `<span class="breadcrumb-current">${escapeHtml(State.currentModel)}</span>`;
    }
    
    els.breadcrumbs.innerHTML = html;

    const bcBrands = document.getElementById('bc-brands');
    if (bcBrands) bcBrands.addEventListener('click', () => loadBrands());

    const bcModels = document.getElementById('bc-models');
    if (bcModels) bcModels.addEventListener('click', () => loadModels(State.currentBrand));
}

function renderCategoryChips() {
    if (!els.categoryChips || !State.currentParts || State.currentParts.length === 0) {
        if (els.categoryChips) els.categoryChips.style.display = 'none';
        return;
    }

    const typesCount = {};
    for (const p of State.currentParts) {
        const t = p.part_type || 'Разное';
        typesCount[t] = (typesCount[t] || 0) + 1;
    }

    const uniqueTypes = Object.keys(typesCount).sort();
    if (uniqueTypes.length <= 1) {
        els.categoryChips.style.display = 'none';
        return;
    }

    els.categoryChips.style.display = 'flex';
    let html = `<div class="chip ${State.currentCategoryChip === 'all' ? 'active' : ''}" data-type="all">Все (${State.currentParts.length})</div>`;

    for (const t of uniqueTypes) {
        const isActive = State.currentCategoryChip === t ? 'active' : '';
        html += `<div class="chip ${isActive}" data-type="${escapeHtml(t)}">${escapeHtml(t)} (${typesCount[t]})</div>`;
    }

    els.categoryChips.innerHTML = html;

    els.categoryChips.querySelectorAll('.chip').forEach(chip => {
        chip.addEventListener('click', () => {
            State.currentCategoryChip = chip.dataset.type;
            tg.HapticFeedback.impactOccurred('light');
            renderCategoryChips();
            renderParts();
        });
    });
}

function goBack() {
    if (State.view === 'cart') {
        showCatalog();
    } else if (State.view === 'catalog') {
        if (State.level === 'parts') {
            loadModels(State.currentBrand);
        } else if (State.level === 'models') {
            loadBrands();
        } else if (State.level === 'search') {
            loadBrands();
        }
    }
}

function showCatalog() {
    State.view = 'catalog';
    els.viewCatalog.classList.add('active');
    els.viewCart.classList.remove('active');
    els.viewSuccess.classList.remove('active');
    updateHeader();
}

function showCart() {
    State.view = 'cart';
    els.viewCatalog.classList.remove('active');
    els.viewCart.classList.add('active');
    els.viewSuccess.classList.remove('active');
    renderCart();
    updateHeader();
}

function showSuccess(orderId) {
    State.view = 'success';
    els.viewCatalog.classList.remove('active');
    els.viewCart.classList.remove('active');
    els.viewSuccess.classList.add('active');
    document.getElementById('success-order-id').textContent = orderId;
    updateHeader();
    
    // Clear cart
    State.cart = [];
    saveCart();
    updateCartBadge();
}

// Data Fetching
async function fetchUserStatus() {
    try {
        const userId = tg.initDataUnsafe?.user?.id || 0;
        if (userId) {
            const res = await fetch(`/api/user_status?user_id=${userId}`);
            const data = await res.json();
            if (data.ok) State.userStatus = data.status;
        }
    } catch (e) {
        console.error('Failed to fetch user status', e);
    }
}

function showLoading(show) {
    els.loading.style.display = show ? 'flex' : 'none';
    if (show) els.catalogList.innerHTML = '';
}

async function loadBrands() {
    State.level = 'brands';
    State.currentBrand = null;
    State.currentModel = null;
    State.currentCategoryChip = 'all';
    els.searchInput.value = '';
    State.searchQuery = '';
    els.btnClearSearch.style.display = 'none';
    updateHeader();
    showLoading(true);
    
    try {
        const res = await fetch('/api/brands');
        const data = await res.json();
        if (data.ok) {
            renderBrands(data.brands);
        }
    } catch (e) {
        console.error(e);
        els.catalogList.innerHTML = '<div class="empty-state">Ошибка загрузки брендов.</div>';
    }
    showLoading(false);
}

async function loadModels(brand) {
    State.level = 'models';
    State.currentBrand = brand;
    State.currentModel = null;
    State.currentCategoryChip = 'all';
    els.searchInput.value = '';
    State.searchQuery = '';
    els.btnClearSearch.style.display = 'none';
    updateHeader();
    showLoading(true);
    
    try {
        const res = await fetch(`/api/models?brand=${encodeURIComponent(brand)}`);
        const data = await res.json();
        if (data.ok) {
            renderModels(data.models);
        }
    } catch (e) {
        console.error(e);
        els.catalogList.innerHTML = '<div class="empty-state">Ошибка загрузки моделей.</div>';
    }
    showLoading(false);
}

async function loadParts(brand, model) {
    State.level = 'parts';
    State.currentBrand = brand;
    State.currentModel = model;
    State.currentCategoryChip = 'all';
    els.searchInput.value = '';
    State.searchQuery = '';
    els.btnClearSearch.style.display = 'none';
    updateHeader();
    showLoading(true);
    
    try {
        const res = await fetch(`/api/parts?brand=${encodeURIComponent(brand)}&model=${encodeURIComponent(model)}`);
        const data = await res.json();
        if (data.ok) {
            State.currentParts = data.parts;
            renderCategoryChips();
            renderParts();
        }
    } catch (e) {
        console.error(e);
        els.catalogList.innerHTML = '<div class="empty-state">Ошибка загрузки запчастей.</div>';
    }
    showLoading(false);
}

async function searchGlobalParts(query) {
    State.level = 'search';
    updateHeader();
    showLoading(true);
    
    try {
        const res = await fetch(`/api/catalog?q=${encodeURIComponent(query)}&page=0&limit=100`);
        const data = await res.json();
        if (data.ok) {
            State.currentParts = data.parts;
            renderParts(true);
        }
    } catch (e) {
        console.error(e);
    }
    showLoading(false);
}

// Rendering
function renderBrands(brands) {
    if (brands.length === 0) {
        els.catalogList.innerHTML = '<div class="empty-state">Бренды не найдены</div>';
        return;
    }
    
    els.catalogList.innerHTML = brands.map(b => `
        <div class="brand-card" data-brand="${escapeHtml(b.name)}">
            <h3 class="card-title">${escapeHtml(b.name)}</h3>
            <span class="card-subtitle">${b.count} моделей</span>
        </div>
    `).join('');

    els.catalogList.querySelectorAll('.brand-card').forEach(card => {
        card.addEventListener('click', () => {
            tg.HapticFeedback.impactOccurred('light');
            loadModels(card.dataset.brand);
        });
    });
}

function renderModels(models) {
    if (models.length === 0) {
        els.catalogList.innerHTML = '<div class="empty-state">Модели не найдены</div>';
        return;
    }
    
    els.catalogList.innerHTML = models.map(m => `
        <div class="model-card" data-model="${escapeHtml(m.name)}">
            <h3 class="card-title">${escapeHtml(m.name)}</h3>
            <span class="card-subtitle">${m.count} запчастей</span>
        </div>
    `).join('');

    els.catalogList.querySelectorAll('.model-card').forEach(card => {
        card.addEventListener('click', () => {
            tg.HapticFeedback.impactOccurred('light');
            loadParts(State.currentBrand, card.dataset.model);
        });
    });
}

function renderParts(showContext = false) {
    let parts = State.currentParts || [];
    
    // Category chip filter
    if (State.level === 'parts' && State.currentCategoryChip !== 'all') {
        parts = parts.filter(p => (p.part_type || 'Разное') === State.currentCategoryChip);
    }

    // Search query filter
    if (State.searchQuery) {
        parts = parts.filter(p => p.name.toLowerCase().includes(State.searchQuery));
    }

    if (parts.length === 0) {
        els.catalogList.innerHTML = `
            <div class="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <p>Ничего не найдено</p>
            </div>
        `;
        return;
    }

    els.catalogList.innerHTML = parts.map(p => {
        const price = State.userStatus === 'wholesale' ? p.wholesale_price : p.retail_price;
        const retailHtml = State.userStatus === 'wholesale' ? `<span class="retail-price">${p.retail_price} ₽</span>` : '';
        const cartItem = State.cart.find(item => item.id === p.id);
        const contextHtml = showContext ? `<div class="part-meta">${escapeHtml(p.category)} &gt; ${escapeHtml(p.subcategory)}</div>` : '';
        const partTypeBadge = p.part_type ? `<span class="part-type-badge">${escapeHtml(p.part_type)}</span>` : '';
        
        return `
            <div class="part-card">
                <div>
                    <h3 class="part-name">${escapeHtml(p.name)}</h3>
                    ${contextHtml}
                    <div class="part-meta">
                        ${partTypeBadge}
                        <span>В наличии: <b>${p.quantity} шт.</b></span>
                    </div>
                </div>
                <div class="part-price-row">
                    <div class="part-price">${price} ₽${retailHtml}</div>
                </div>
                ${cartItem 
                    ? `<div class="quantity-control">
                        <button class="qty-btn" data-action="dec" data-id="${p.id}">-</button>
                        <span class="qty-val">${cartItem.qty}</span>
                        <button class="qty-btn" data-action="inc" data-id="${p.id}">+</button>
                       </div>`
                    : `<button class="add-to-cart-btn" data-action="add" data-id="${p.id}" ${p.quantity === 0 ? 'disabled' : ''}>
                        ${p.quantity === 0 ? 'Нет в наличии' : 'В корзину'}
                       </button>`
                }
            </div>
        `;
    }).join('');

    // Attach click listeners safely
    els.catalogList.querySelectorAll('.add-to-cart-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.id, 10);
            const part = State.currentParts.find(item => item.id === id);
            if (part) addToCart(part);
        });
    });

    els.catalogList.querySelectorAll('.qty-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.id, 10);
            const delta = btn.dataset.action === 'inc' ? 1 : -1;
            updateQty(id, delta);
        });
    });
}

// Cart Logic
function addToCart(part) {
    if (part.quantity <= 0) return;
    const existing = State.cart.find(item => item.id === part.id);
    if (existing) {
        if (existing.qty < part.quantity) {
            existing.qty += 1;
        } else {
            tg.HapticFeedback.notificationOccurred('warning');
            return;
        }
    } else {
        State.cart.push({ ...part, qty: 1 });
    }
    saveCart();
    renderParts(State.level === 'search');
    updateCartBadge();
    tg.HapticFeedback.impactOccurred('light');
}

function updateQty(id, delta) {
    const item = State.cart.find(i => i.id === id);
    if (!item) return;
    
    item.qty += delta;
    
    if (item.qty > item.quantity) {
        item.qty = item.quantity;
        tg.HapticFeedback.notificationOccurred('warning');
    } else if (item.qty <= 0) {
        State.cart = State.cart.filter(i => i.id !== id);
        tg.HapticFeedback.impactOccurred('medium');
    } else {
        tg.HapticFeedback.impactOccurred('light');
    }
    
    saveCart();
    if (State.view === 'cart') renderCart();
    else renderParts(State.level === 'search');
    updateCartBadge();
}

function updateCartBadge() {
    const totalQty = State.cart.reduce((sum, item) => sum + item.qty, 0);
    els.cartBadge.textContent = totalQty;
    els.cartBadge.style.display = totalQty > 0 ? 'block' : 'none';
}

function saveCart() {
    try {
        localStorage.setItem('partsbot_cart', JSON.stringify(State.cart));
    } catch (e) {}
}

function loadCart() {
    try {
        const saved = localStorage.getItem('partsbot_cart');
        if (saved) State.cart = JSON.parse(saved);
        updateCartBadge();
    } catch (e) {}
}

function renderCart() {
    const cartList = document.getElementById('cart-items');
    
    if (State.cart.length === 0) {
        cartList.innerHTML = `
            <div class="empty-state">
                <p>Корзина пуста</p>
                <button class="add-to-cart-btn" style="width: auto; margin-top: 16px;" id="btn-cart-to-catalog">Перейти в каталог</button>
            </div>
        `;
        const btnBack = document.getElementById('btn-cart-to-catalog');
        if (btnBack) btnBack.addEventListener('click', () => showCatalog());
        document.querySelector('.checkout-form').style.display = 'none';
        document.getElementById('cart-total').textContent = '0 ₽';
        tg.MainButton.hide();
        return;
    }
    
    document.querySelector('.checkout-form').style.display = 'block';
    
    let total = 0;
    cartList.innerHTML = State.cart.map(item => {
        const price = State.userStatus === 'wholesale' ? item.wholesale_price : item.retail_price;
        total += price * item.qty;
        
        return `
            <div class="cart-item glass-panel">
                <div class="cart-item-details">
                    <div class="cart-item-name">${escapeHtml(item.name)}</div>
                    <div class="cart-item-price">${price} ₽ × ${item.qty} = ${price * item.qty} ₽</div>
                </div>
                <div class="cart-item-actions">
                    <div class="quantity-control" style="width: 100px;">
                        <button class="qty-btn" data-action="dec" data-id="${item.id}">-</button>
                        <span class="qty-val">${item.qty}</span>
                        <button class="qty-btn" data-action="inc" data-id="${item.id}">+</button>
                    </div>
                    <button class="remove-btn icon-btn" data-action="del" data-id="${item.id}">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>
        `;
    }).join('');

    cartList.querySelectorAll('.qty-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.id, 10);
            const delta = btn.dataset.action === 'inc' ? 1 : -1;
            updateQty(id, delta);
        });
    });

    cartList.querySelectorAll('.remove-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = parseInt(btn.dataset.id, 10);
            const item = State.cart.find(i => i.id === id);
            if (item) updateQty(id, -item.qty);
        });
    });
    
    document.getElementById('cart-total').textContent = `${total} ₽`;
    tg.MainButton.text = `ОФОРМИТЬ ЗАКАЗ НА ${total} ₽`;
    tg.MainButton.show();
}

// Order Submission
async function submitOrder() {
    if (State.cart.length === 0) return;
    
    const deliveryMethod = els.deliveryMethod.value;
    const deliveryAddress = document.getElementById('delivery_address').value;
    const contact = document.getElementById('contact').value;
    const paymentMethod = document.getElementById('payment_method').value;
    const notes = document.getElementById('notes').value;
    
    if (!contact.trim()) {
        tg.showAlert('Пожалуйста, укажите контактный телефон.');
        return;
    }
    
    if (deliveryMethod !== 'pickup' && !deliveryAddress.trim()) {
        tg.showAlert('Пожалуйста, укажите адрес доставки.');
        return;
    }
    
    tg.MainButton.showProgress();
    
    const user = tg.initDataUnsafe?.user;
    let userName = 'Web Client';
    if (user) {
        if (user.first_name) {
            userName = user.first_name + (user.last_name ? ` ${user.last_name}` : '');
        } else if (user.username) {
            userName = `@${user.username}`;
        } else if (user.id) {
            userName = `ID ${user.id}`;
        }
    }

    const orderData = {
        user_id: user?.id || 0,
        user_name: userName,
        contact: contact.trim(),
        delivery_method: deliveryMethod,
        delivery_address: deliveryAddress.trim(),
        payment_method: paymentMethod,
        notes: notes.trim(),
        items: State.cart
    };
    
    try {
        const response = await fetch('/api/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(orderData)
        });
        
        const result = await response.json();
        
        if (result.ok) {
            tg.HapticFeedback.notificationOccurred('success');
            showSuccess(result.order_id);
        } else {
            tg.showAlert('Ошибка: ' + (result.error || 'Не удалось оформить заказ.'));
        }
    } catch (e) {
        tg.showAlert('Ошибка сети. Попробуйте позже.');
    } finally {
        tg.MainButton.hideProgress();
    }
}

// Start
loadCart();
init();
