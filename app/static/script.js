const API_BASE = "";

// Global store to avoid passing complex objects in onclick
window.customersMap = {};

document.addEventListener('DOMContentLoaded', () => {
    // 1. Initial Load
    fetchAllCustomers();
    setupModal();

    // 2. Search Listener
    const searchInput = document.getElementById('searchInput');
    let debounceTimer;
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => performSearch(e.target.value), 300);
        });
    }

    // 3. Form Submission
    const form = document.getElementById('customerForm');
    if (form) {
        form.addEventListener('submit', handleFormSubmit);
    }
    
    // 4. PDF Download
    const downloadBtn = document.getElementById('downloadPdfBtn');
    if (downloadBtn) {
        downloadBtn.addEventListener('click', () => {
            window.location.href = `${API_BASE}/download-pdf`;
        });
    }
});

// --- Modal Management ---
function setupModal() {
    const modal = document.getElementById('customerModal');
    const openBtn = document.getElementById('openAddModalBtn');
    const closeBtn = document.getElementById('closeModalBtn');
    const cancelBtn = document.getElementById('cancelBtn');

    if (openBtn) openBtn.addEventListener('click', () => openModal(false));
    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

    // Click outside to close
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal();
        });
    }
}

function openModal(isEdit, customerId = null) {
    const modal = document.getElementById('customerModal');
    const title = document.getElementById('modalTitle');
    const form = document.getElementById('customerForm');

    // Retrieve data if editing
    let data = null;
    if (isEdit && customerId) {
        data = window.customersMap[customerId];
    }

    if (isEdit && data) {
        title.textContent = 'Edit Customer';
        document.getElementById('customerId').value = data.id || data.mongo_id;
        if (data.date) {
            document.getElementById('date').value = new Date(data.date).toISOString().split('T')[0];
        } else {
             document.getElementById('date').value = '';
        }
        document.getElementById('customer_name').value = data.customer_name || '';
        document.getElementById('customer_address').value = data.customer_address || '';
        document.getElementById('customer_number').value = data.customer_number || '';
        document.getElementById('jar').value = data.jar || 0;
        document.getElementById('paid').value = data.paid || 0.0;
        document.getElementById('pending').value = data.pending || 0.0;
        document.getElementById('success').value = data.success ? 'true' : 'false';
        document.getElementById('is_monthly').checked = data.is_monthly || false;
    } else {
        title.textContent = 'New Customer';
        form.reset();
        document.getElementById('customerId').value = '';
        // Default to today
        document.getElementById('date').value = new Date().toISOString().split('T')[0];
        document.getElementById('is_monthly').checked = false;
    }

    // Add active class for animation
    if (modal) {
        modal.classList.add('active');
        modal.style.display = 'flex';
    }
}

function closeModal() {
    const modal = document.getElementById('customerModal');
    if (modal) {
        modal.classList.remove('active');
        setTimeout(() => {
            modal.style.display = 'none';
        }, 200); // Wait for transition
    }
}

// --- Data Handling ---
async function handleFormSubmit(e) {
    e.preventDefault();
    
    // Collect Data
    const id = document.getElementById('customerId').value;
    const isEdit = !!id;
    
    const payload = {
        date: document.getElementById('date').value || null,
        customer_name: document.getElementById('customer_name').value,
        customer_address: document.getElementById('customer_address').value,
        customer_number: document.getElementById('customer_number').value,
        jar: parseInt(document.getElementById('jar').value) || 0,
        paid: parseFloat(document.getElementById('paid').value) || 0.0,
        pending: parseFloat(document.getElementById('pending').value) || 0.0,
        success: document.getElementById('success').value === 'true',
        is_monthly: document.getElementById('is_monthly').checked
    };

    const endpoint = isEdit ? `${API_BASE}/update/${id}` : `${API_BASE}/add-customer`;
    const method = isEdit ? 'PUT' : 'POST';

    try {
        const res = await fetch(endpoint, {
            method: method,
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            closeModal();
            fetchAllCustomers(); // Refresh table
        } else {
            const err = await res.json();
            alert('Error: ' + JSON.stringify(err));
        }
    } catch (err) {
        console.error(err);
        alert('Failed to connect to server');
    }
}

async function fetchAllCustomers() {
    try {
        const res = await fetch(`${API_BASE}/all-customers`);
        if (!res.ok) throw new Error('Network response was not ok');
        const data = await res.json();
        renderDashboard(data);
    } catch (err) {
        console.error('Fetch error:', err);
    }
}

async function performSearch(query) {
    if (!query) return fetchAllCustomers();
    try {
        const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query)}`);
        const data = await res.json();
        renderDashboard(data); // Search results
    } catch (err) { console.error(err); }
}

// --- Rendering ---
function renderDashboard(customers) {
    // Clear map
    window.customersMap = {};

    // 1. Update Stats
    let totalJars = 0, totalRevenue = 0, totalPending = 0;
    
    if (customers && customers.length) {
        customers.forEach(current => {
            totalJars += (current.jar || 0);
            totalRevenue += (current.paid || 0);
            totalPending += (current.pending || 0);
            
            // Store in map
            const id = current.id || current.mongo_id;
            if (id) window.customersMap[id] = current;
        });
    }

    const setContent = (id, text) => {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    };

    setContent('totalCustomers', customers ? customers.length : 0);
    setContent('totalJars', totalJars);
    setContent('totalRevenue', formatCurrency(totalRevenue));
    setContent('totalPending', formatCurrency(totalPending));

    // 2. Render Table
    const tbody = document.getElementById('customerTableBody');
    if (tbody) {
        tbody.innerHTML = '';

        if (!customers || !customers.length) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 2rem; color: #64748b;">No customers found.</td></tr>';
            return;
        }

        customers.forEach(c => {
            const tr = document.createElement('tr');
            const id = c.id || c.mongo_id;
            const statusColor = c.success ? 'color: #166534; background: #dcfce7;' : 'color: #854d0e; background: #fef9c3;';
            const statusText = c.success ? 'Active' : 'Pending';
            
            // Format Last Updated
            const dateStr = c.updated_at || c.created_at || '';
            const formattedDate = dateStr ? timeAgo(dateStr) : 'N/A';

            // Format Live Date
            const liveDate = c.date ? new Date(c.date).toLocaleDateString() : 'N/A';

            // Safe onclick calls using ID only
            tr.innerHTML = `
                <td>
                    <div class="cell-date">${liveDate}</div>
                </td>
                <td>
                    <div class="cell-name">${escapeHtml(c.customer_name)}</div>
                </td>
                <td>
                    <div class="cell-address">${escapeHtml(c.customer_address)}</div>
                </td>
                <td>${escapeHtml(c.customer_number)}</td>
                <td>
                    <span style="padding: 2px 8px; border-radius: 99px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; ${statusColor}">
                        ${statusText}
                    </span>
                </td>
                <td>
                    <div style="display: flex; gap: 8px; font-size: 0.8rem; font-weight: 500;">
                        <span style="background: #eff6ff; color: #3b82f6; padding: 2px 6px; border-radius: 4px;">Jar: ${c.jar}</span>
                        <span style="background: #ecfdf5; color: #10b981; padding: 2px 6px; border-radius: 4px;">Paid: $${c.paid}</span>
                         <span style="background: #fffbeb; color: #b45309; padding: 2px 6px; border-radius: 4px;">Pending: $${c.pending}</span>
                    </div>
                </td>
                <td>
                    <div style="font-size: 0.85rem; color: #64748b;">${formattedDate}</div>
                </td>
                <td class="text-right">
                    <button class="btn-icon" onclick="window.downloadCustomerPdf('${id}')" title="Download Ledger">
                        <ion-icon name="document-text-outline"></ion-icon>
                    </button>
                    <button class="btn-icon" onclick="window.editCustomer('${id}')">
                        <ion-icon name="create-outline"></ion-icon>
                    </button>
                    <button class="btn-icon danger" onclick="window.deleteCustomer('${id}')">
                        <ion-icon name="trash-outline"></ion-icon>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    }
}

function timeAgo(dateString) {
    if (!dateString) return '';
    const date = new Date(dateString);
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);

    let interval = Math.floor(seconds / 31536000);
    if (interval >= 1) return interval + "y ago";
    interval = Math.floor(seconds / 2592000);
    if (interval >= 1) return interval + "mo ago";
    interval = Math.floor(seconds / 86400);
    if (interval >= 1) return interval + "d ago";
    interval = Math.floor(seconds / 3600);
    if (interval >= 1) return interval + "h ago";
    interval = Math.floor(seconds / 60);
    if (interval >= 1) return interval + "m ago";
    return "Just now";
}

function formatCurrency(num) {
    return '$' + num.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
}

function escapeHtml(text) {
    if (!text) return '';
    return text.toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Global Actions
window.editCustomer = function(id) {
    openModal(true, id);
};

window.deleteCustomer = async function(id) {
    if (confirm('Are you sure you want to delete this customer?')) {
        await fetch(`${API_BASE}/delete/${id}`, { method: 'DELETE' });
        fetchAllCustomers();
    }
};

window.downloadCustomerPdf = function(id) {
    window.location.href = `${API_BASE}/monthly/download-pdf/${id}`;
};
