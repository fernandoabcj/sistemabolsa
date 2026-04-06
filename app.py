import os
import hashlib
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_from_directory, jsonify
)
from werkzeug.utils import secure_filename
import sqlite3
import openpyxl

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DATABASE = os.path.join(os.path.dirname(__file__), 'bolsas.db')

MESES = [
    (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
    (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
    (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro')
]


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            nome TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            ativo INTEGER DEFAULT 1,
            criado_em TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS usuario_pi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            pi_codigo TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            UNIQUE(usuario_id, pi_codigo)
        );

        CREATE TABLE IF NOT EXISTS dados_siafi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ug_codigo TEXT,
            ug_nome TEXT,
            acao_codigo TEXT,
            acao_descricao TEXT,
            po_codigo TEXT,
            po_descricao TEXT,
            ptres TEXT,
            fonte_codigo TEXT,
            fonte_descricao TEXT,
            pi_codigo TEXT,
            pi_descricao TEXT,
            nd_codigo TEXT,
            nd_descricao TEXT,
            credito_disponivel REAL DEFAULT 0,
            despesas_empenhadas REAL DEFAULT 0,
            despesas_liquidadas REAL DEFAULT 0,
            despesas_liquidadas_pagar REAL DEFAULT 0,
            despesas_pagas REAL DEFAULT 0,
            restos_pagar REAL DEFAULT 0,
            data_upload TEXT,
            arquivo_origem TEXT
        );

        CREATE TABLE IF NOT EXISTS uploads_siafi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_arquivo TEXT NOT NULL,
            data_upload TEXT DEFAULT (datetime('now', 'localtime')),
            usuario_id INTEGER,
            registros_importados INTEGER DEFAULT 0,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS lancamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            pi_codigo TEXT NOT NULL,
            ano INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            valor_previsao REAL DEFAULT 0,
            valor_pagamento REAL DEFAULT 0,
            observacao TEXT,
            atualizado_em TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            UNIQUE(usuario_id, pi_codigo, ano, mes)
        );

        CREATE INDEX IF NOT EXISTS idx_siafi_pi ON dados_siafi(pi_codigo);
        CREATE INDEX IF NOT EXISTS idx_lancamentos_pi ON lancamentos(pi_codigo, ano, mes);
        CREATE INDEX IF NOT EXISTS idx_usuario_pi ON usuario_pi(usuario_id);
    ''')

    # Criar admin padrão se não existir
    admin = conn.execute("SELECT id FROM usuarios WHERE login = 'codeor'").fetchone()
    if not admin:
        conn.execute(
            "INSERT INTO usuarios (login, senha, nome, is_admin) VALUES (?, ?, ?, 1)",
            ('codeor', hash_password('Codeor01@'), 'Administrador CODEOR')
        )
    conn.commit()
    conn.close()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ==================== AUTENTICAÇÃO ====================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_val = request.form.get('login', '').strip()
        senha = request.form.get('senha', '')
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM usuarios WHERE login = ? AND senha = ? AND ativo = 1",
            (login_val, hash_password(senha))
        ).fetchone()
        conn.close()
        if user:
            session['user_id'] = user['id']
            session['user_nome'] = user['nome']
            session['user_login'] = user['login']
            session['is_admin'] = bool(user['is_admin'])
            flash(f'Bem-vindo, {user["nome"]}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Login ou senha inválidos.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Logout realizado com sucesso.', 'info')
    return redirect(url_for('login'))


# ==================== DASHBOARD ====================

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    stats = {}

    if session.get('is_admin'):
        stats['total_pis'] = conn.execute(
            "SELECT COUNT(DISTINCT pi_codigo) FROM dados_siafi"
        ).fetchone()[0]
        stats['total_usuarios'] = conn.execute(
            "SELECT COUNT(*) FROM usuarios WHERE is_admin = 0"
        ).fetchone()[0]
        stats['total_uploads'] = conn.execute(
            "SELECT COUNT(*) FROM uploads_siafi"
        ).fetchone()[0]
        stats['ultimo_upload'] = conn.execute(
            "SELECT data_upload FROM uploads_siafi ORDER BY id DESC LIMIT 1"
        ).fetchone()
        stats['total_credito'] = conn.execute(
            "SELECT COALESCE(SUM(credito_disponivel), 0) FROM dados_siafi"
        ).fetchone()[0]
        stats['total_empenhado'] = conn.execute(
            "SELECT COALESCE(SUM(despesas_empenhadas), 0) FROM dados_siafi"
        ).fetchone()[0]
        stats['total_pago'] = conn.execute(
            "SELECT COALESCE(SUM(despesas_pagas), 0) FROM dados_siafi"
        ).fetchone()[0]
    else:
        user_pis = conn.execute(
            "SELECT pi_codigo FROM usuario_pi WHERE usuario_id = ?",
            (session['user_id'],)
        ).fetchall()
        pi_list = [p['pi_codigo'] for p in user_pis]
        stats['meus_pis'] = len(pi_list)

        if pi_list:
            placeholders = ','.join('?' * len(pi_list))
            stats['total_previsao'] = conn.execute(
                f"SELECT COALESCE(SUM(valor_previsao), 0) FROM lancamentos WHERE pi_codigo IN ({placeholders}) AND ano = ?",
                pi_list + [datetime.now().year]
            ).fetchone()[0]
            stats['total_pagamento'] = conn.execute(
                f"SELECT COALESCE(SUM(valor_pagamento), 0) FROM lancamentos WHERE pi_codigo IN ({placeholders}) AND ano = ?",
                pi_list + [datetime.now().year]
            ).fetchone()[0]
        else:
            stats['total_previsao'] = 0
            stats['total_pagamento'] = 0

    conn.close()
    return render_template('dashboard.html', stats=stats)


# ==================== UPLOAD SIAFI (ADMIN) ====================

@app.route('/admin/upload', methods=['GET', 'POST'])
@admin_required
def upload_siafi():
    if request.method == 'POST':
        if 'arquivo' not in request.files:
            flash('Nenhum arquivo selecionado.', 'danger')
            return redirect(request.url)

        arquivo = request.files['arquivo']
        if arquivo.filename == '':
            flash('Nenhum arquivo selecionado.', 'danger')
            return redirect(request.url)

        if not arquivo.filename.endswith(('.xlsx', '.xls')):
            flash('Formato inválido. Envie arquivo .xlsx ou .xls.', 'danger')
            return redirect(request.url)

        filename = secure_filename(arquivo.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        arquivo.save(filepath)

        try:
            registros = processar_arquivo_siafi(filepath, filename)
            flash(f'Arquivo importado com sucesso! {registros} registros processados.', 'success')
        except Exception as e:
            flash(f'Erro ao processar arquivo: {str(e)}', 'danger')

        return redirect(url_for('upload_siafi'))

    conn = get_db()
    uploads = conn.execute(
        "SELECT * FROM uploads_siafi ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return render_template('upload_siafi.html', uploads=uploads)


def processar_arquivo_siafi(filepath, filename):
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb[wb.sheetnames[0]]
    conn = get_db()

    # Limpar dados anteriores antes de importar novos
    conn.execute("DELETE FROM dados_siafi")

    registros = 0
    header_row = 8  # Dados começam na linha 8

    for row in ws.iter_rows(min_row=header_row, max_row=ws.max_row, values_only=False):
        pi_codigo = row[9].value  # Coluna J
        if not pi_codigo:
            continue

        conn.execute('''
            INSERT INTO dados_siafi (
                ug_codigo, ug_nome, acao_codigo, acao_descricao,
                po_codigo, po_descricao, ptres, fonte_codigo, fonte_descricao,
                pi_codigo, pi_descricao, nd_codigo, nd_descricao,
                credito_disponivel, despesas_empenhadas, despesas_liquidadas,
                despesas_liquidadas_pagar, despesas_pagas, restos_pagar,
                data_upload, arquivo_origem
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'), ?)
        ''', (
            row[0].value, row[1].value, row[2].value, row[3].value,
            row[4].value, row[5].value, row[6].value, row[7].value,
            row[8].value, row[9].value, row[10].value, row[11].value,
            row[12].value,
            row[13].value or 0, row[14].value or 0, row[15].value or 0,
            row[16].value or 0, row[17].value or 0, row[18].value or 0,
            filename
        ))
        registros += 1

    conn.execute(
        "INSERT INTO uploads_siafi (nome_arquivo, usuario_id, registros_importados) VALUES (?, ?, ?)",
        (filename, session['user_id'], registros)
    )
    conn.commit()
    conn.close()
    return registros


@app.route('/admin/dados-siafi')
@admin_required
def dados_siafi():
    conn = get_db()
    dados = conn.execute('''
        SELECT * FROM dados_siafi ORDER BY pi_descricao
    ''').fetchall()
    conn.close()
    return render_template('dados_siafi.html', dados=dados)


# ==================== GERENCIAR USUÁRIOS (ADMIN) ====================

@app.route('/admin/usuarios')
@admin_required
def listar_usuarios():
    conn = get_db()
    usuarios = conn.execute('''
        SELECT u.*, GROUP_CONCAT(up.pi_codigo, ', ') as pis_vinculados
        FROM usuarios u
        LEFT JOIN usuario_pi up ON u.id = up.usuario_id
        WHERE u.is_admin = 0
        GROUP BY u.id
        ORDER BY u.nome
    ''').fetchall()
    conn.close()
    return render_template('usuarios.html', usuarios=usuarios)


@app.route('/admin/usuarios/novo', methods=['GET', 'POST'])
@admin_required
def criar_usuario():
    conn = get_db()
    if request.method == 'POST':
        login_val = request.form.get('login', '').strip()
        senha = request.form.get('senha', '')
        nome = request.form.get('nome', '').strip()
        pis_selecionados = request.form.getlist('pis')

        if not login_val or not senha or not nome:
            flash('Preencha todos os campos obrigatórios.', 'danger')
        else:
            try:
                cursor = conn.execute(
                    "INSERT INTO usuarios (login, senha, nome) VALUES (?, ?, ?)",
                    (login_val, hash_password(senha), nome)
                )
                user_id = cursor.lastrowid
                for pi in pis_selecionados:
                    conn.execute(
                        "INSERT INTO usuario_pi (usuario_id, pi_codigo) VALUES (?, ?)",
                        (user_id, pi)
                    )
                conn.commit()
                flash(f'Usuário "{nome}" criado com sucesso!', 'success')
                conn.close()
                return redirect(url_for('listar_usuarios'))
            except sqlite3.IntegrityError:
                flash('Login já existe. Escolha outro.', 'danger')

    pis_disponiveis = conn.execute(
        "SELECT DISTINCT pi_codigo, pi_descricao FROM dados_siafi ORDER BY pi_descricao"
    ).fetchall()
    conn.close()
    return render_template('criar_usuario.html', pis_disponiveis=pis_disponiveis)


@app.route('/admin/usuarios/<int:user_id>/editar', methods=['GET', 'POST'])
@admin_required
def editar_usuario(user_id):
    conn = get_db()
    usuario = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    if not usuario:
        flash('Usuário não encontrado.', 'danger')
        conn.close()
        return redirect(url_for('listar_usuarios'))

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        senha = request.form.get('senha', '').strip()
        ativo = 1 if request.form.get('ativo') else 0
        pis_selecionados = request.form.getlist('pis')

        if nome:
            conn.execute("UPDATE usuarios SET nome = ?, ativo = ? WHERE id = ?", (nome, ativo, user_id))
        if senha:
            conn.execute("UPDATE usuarios SET senha = ? WHERE id = ?", (hash_password(senha), user_id))

        conn.execute("DELETE FROM usuario_pi WHERE usuario_id = ?", (user_id,))
        for pi in pis_selecionados:
            conn.execute(
                "INSERT INTO usuario_pi (usuario_id, pi_codigo) VALUES (?, ?)",
                (user_id, pi)
            )
        conn.commit()
        flash('Usuário atualizado com sucesso!', 'success')
        conn.close()
        return redirect(url_for('listar_usuarios'))

    pis_disponiveis = conn.execute(
        "SELECT DISTINCT pi_codigo, pi_descricao FROM dados_siafi ORDER BY pi_descricao"
    ).fetchall()
    pis_usuario = [row['pi_codigo'] for row in conn.execute(
        "SELECT pi_codigo FROM usuario_pi WHERE usuario_id = ?", (user_id,)
    ).fetchall()]
    conn.close()
    return render_template('editar_usuario.html', usuario=usuario,
                         pis_disponiveis=pis_disponiveis, pis_usuario=pis_usuario)


@app.route('/admin/usuarios/<int:user_id>/excluir', methods=['POST'])
@admin_required
def excluir_usuario(user_id):
    conn = get_db()
    conn.execute("DELETE FROM usuario_pi WHERE usuario_id = ?", (user_id,))
    conn.execute("DELETE FROM lancamentos WHERE usuario_id = ?", (user_id,))
    conn.execute("DELETE FROM usuarios WHERE id = ? AND is_admin = 0", (user_id,))
    conn.commit()
    conn.close()
    flash('Usuário excluído com sucesso!', 'success')
    return redirect(url_for('listar_usuarios'))


# ==================== LANÇAMENTOS (USUÁRIOS) ====================

@app.route('/lancamentos')
@login_required
def lancamentos():
    conn = get_db()
    ano = request.args.get('ano', datetime.now().year, type=int)

    if session.get('is_admin'):
        pis = conn.execute(
            "SELECT DISTINCT pi_codigo, pi_descricao FROM dados_siafi ORDER BY pi_descricao"
        ).fetchall()
    else:
        pis = conn.execute('''
            SELECT DISTINCT ds.pi_codigo, ds.pi_descricao
            FROM dados_siafi ds
            INNER JOIN usuario_pi up ON ds.pi_codigo = up.pi_codigo
            WHERE up.usuario_id = ?
            ORDER BY ds.pi_descricao
        ''', (session['user_id'],)).fetchall()

    # Buscar lançamentos de todos os PIs para o ano selecionado
    lancamentos_data = {}
    all_lancamentos = conn.execute(
        "SELECT * FROM lancamentos WHERE ano = ?", (ano,)
    ).fetchall()
    for l in all_lancamentos:
        key = l['pi_codigo']
        if key not in lancamentos_data:
            lancamentos_data[key] = {}
        lancamentos_data[key][l['mes']] = dict(l)

    # Buscar dados SIAFI agregados por PI (soma das colunas por PI)
    siafi_por_pi = {}
    siafi_rows = conn.execute('''
        SELECT pi_codigo,
               COALESCE(SUM(credito_disponivel), 0) as total_credito,
               COALESCE(SUM(despesas_empenhadas), 0) as total_empenhado,
               COALESCE(SUM(despesas_liquidadas), 0) as total_liquidado,
               COALESCE(SUM(despesas_pagas), 0) as total_pago
        FROM dados_siafi
        GROUP BY pi_codigo
    ''').fetchall()
    for s in siafi_rows:
        siafi_por_pi[s['pi_codigo']] = dict(s)

    conn.close()
    return render_template('lancamentos.html', pis=pis, ano=ano, meses=MESES,
                         lancamentos_data=lancamentos_data, siafi_por_pi=siafi_por_pi)


@app.route('/lancamentos/<pi_codigo>')
@login_required
def lancamento_pi(pi_codigo):
    conn = get_db()
    ano = request.args.get('ano', datetime.now().year, type=int)

    # Verificar acesso
    if not session.get('is_admin'):
        acesso = conn.execute(
            "SELECT 1 FROM usuario_pi WHERE usuario_id = ? AND pi_codigo = ?",
            (session['user_id'], pi_codigo)
        ).fetchone()
        if not acesso:
            flash('Você não tem acesso a este PI.', 'danger')
            conn.close()
            return redirect(url_for('lancamentos'))

    pi_info = conn.execute(
        "SELECT DISTINCT pi_codigo, pi_descricao FROM dados_siafi WHERE pi_codigo = ?",
        (pi_codigo,)
    ).fetchone()

    siafi_data = conn.execute(
        "SELECT * FROM dados_siafi WHERE pi_codigo = ?",
        (pi_codigo,)
    ).fetchall()

    lancamentos_existentes = {}
    rows = conn.execute(
        "SELECT * FROM lancamentos WHERE pi_codigo = ? AND ano = ?",
        (pi_codigo, ano)
    ).fetchall()
    for row in rows:
        lancamentos_existentes[row['mes']] = dict(row)

    conn.close()
    return render_template('lancamento_pi.html',
                         pi_info=pi_info, siafi_data=siafi_data,
                         lancamentos=lancamentos_existentes,
                         ano=ano, meses=MESES)


@app.route('/lancamentos/salvar', methods=['POST'])
@login_required
def salvar_lancamento():
    pi_codigo = request.form.get('pi_codigo')
    ano = request.form.get('ano', type=int)
    conn = get_db()

    # Verificar acesso
    if not session.get('is_admin'):
        acesso = conn.execute(
            "SELECT 1 FROM usuario_pi WHERE usuario_id = ? AND pi_codigo = ?",
            (session['user_id'], pi_codigo)
        ).fetchone()
        if not acesso:
            flash('Você não tem acesso a este PI.', 'danger')
            conn.close()
            return redirect(url_for('lancamentos'))

    for mes_num in range(1, 13):
        previsao = request.form.get(f'previsao_{mes_num}', '0')
        pagamento = request.form.get(f'pagamento_{mes_num}', '0')
        observacao = request.form.get(f'obs_{mes_num}', '').strip()

        try:
            previsao = float(previsao.replace('.', '').replace(',', '.')) if previsao else 0
        except ValueError:
            previsao = 0
        try:
            pagamento = float(pagamento.replace('.', '').replace(',', '.')) if pagamento else 0
        except ValueError:
            pagamento = 0

        conn.execute('''
            INSERT INTO lancamentos (usuario_id, pi_codigo, ano, mes, valor_previsao, valor_pagamento, observacao)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(usuario_id, pi_codigo, ano, mes)
            DO UPDATE SET
                valor_previsao = excluded.valor_previsao,
                valor_pagamento = excluded.valor_pagamento,
                observacao = excluded.observacao,
                atualizado_em = datetime('now', 'localtime')
        ''', (session['user_id'], pi_codigo, ano, mes_num, previsao, pagamento, observacao))

    conn.commit()
    conn.close()
    flash('Lançamentos salvos com sucesso!', 'success')
    return redirect(url_for('lancamento_pi', pi_codigo=pi_codigo, ano=ano))


# ==================== RELATÓRIOS ====================

@app.route('/relatorios')
@login_required
def relatorios():
    conn = get_db()
    ano = request.args.get('ano', datetime.now().year, type=int)

    if session.get('is_admin'):
        dados = conn.execute('''
            SELECT ds.pi_codigo, ds.pi_descricao,
                   ds.credito_disponivel, ds.despesas_empenhadas,
                   ds.despesas_liquidadas, ds.despesas_pagas,
                   COALESCE(SUM(l.valor_previsao), 0) as total_previsao,
                   COALESCE(SUM(l.valor_pagamento), 0) as total_pagamento
            FROM dados_siafi ds
            LEFT JOIN lancamentos l ON ds.pi_codigo = l.pi_codigo AND l.ano = ?
            GROUP BY ds.pi_codigo
            ORDER BY ds.pi_descricao
        ''', (ano,)).fetchall()
    else:
        dados = conn.execute('''
            SELECT ds.pi_codigo, ds.pi_descricao,
                   ds.credito_disponivel, ds.despesas_empenhadas,
                   ds.despesas_liquidadas, ds.despesas_pagas,
                   COALESCE(SUM(l.valor_previsao), 0) as total_previsao,
                   COALESCE(SUM(l.valor_pagamento), 0) as total_pagamento
            FROM dados_siafi ds
            INNER JOIN usuario_pi up ON ds.pi_codigo = up.pi_codigo AND up.usuario_id = ?
            LEFT JOIN lancamentos l ON ds.pi_codigo = l.pi_codigo AND l.ano = ?
            GROUP BY ds.pi_codigo
            ORDER BY ds.pi_descricao
        ''', (session['user_id'], ano)).fetchall()

    conn.close()
    return render_template('relatorios.html', dados=dados, ano=ano)


# ==================== API ENDPOINTS ====================

@app.route('/api/pis')
@admin_required
def api_pis():
    conn = get_db()
    pis = conn.execute(
        "SELECT DISTINCT pi_codigo, pi_descricao FROM dados_siafi ORDER BY pi_descricao"
    ).fetchall()
    conn.close()
    return jsonify([dict(p) for p in pis])


if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
