# -*- coding: utf-8 -*-

from flask import Flask, render_template, request, jsonify
import pandas as pd
import re
from datetime import datetime
import io
import os
import json
import firebase_admin
from firebase_admin import credentials, auth

# Inicializa a aplicação Flask
app = Flask(__name__)

# Inicializa o Firebase Admin SDK
try:
    # Método para produção (OnRender): Carrega a partir de variável de ambiente
    creds_json_str = os.environ.get('FIREBASE_CREDENTIALS_JSON')
    if creds_json_str:
        creds_dict = json.loads(creds_json_str)
        cred = credentials.Certificate(creds_dict)
    else:
        # Método para desenvolvimento local: Carrega a partir do arquivo
        cred = credentials.Certificate('firebase-credentials.json')
        
    firebase_admin.initialize_app(cred)
    print("Firebase Admin SDK inicializado com sucesso.")
except Exception as e:
    print(f"ERRO: Falha ao inicializar o Firebase Admin SDK: {e}")


def parse_data_file(file_content):
    """
    Analisa o conteúdo de um arquivo de dados e extrai os registros de voo.
    Se uma data for inválida, o campo 'timestamp' ficará nulo, mas o resto
    da linha será processado e incluído no resultado.
    """
    lines = file_content.split('\n')
    records = []
    
    for line in lines:
        if len(line.strip()) <= 50 or line.startswith('SBIZAIZ0'):
            continue

        record = {
            'timestamp': None, 'matricula': 'N/A', 'tipo_aeronave': 'N/A',
            'destino': 'N/A', 'regra_voo': 'N/A', 'pista': '', 'responsavel': 'N/A'
        }

        try:
            # Extrai o operador (última palavra na linha)
            operator_match = re.search(r'\S+$', line.strip())
            if operator_match:
                record['responsavel'] = operator_match.group(0)

            # Extrai matrícula e tipo de aeronave (posições fixas)
            record['matricula'] = line[15:22].strip()
            record['tipo_aeronave'] = line[22:27].strip()

            # Encontra a regra de voo para ancorar a análise
            rule_match = re.search(r'(IV|VV)', line)
            if rule_match:
                record['regra_voo'] = rule_match.group(0).replace('IV', 'IFR').replace('VV', 'VFR')
                rule_index = rule_match.start()
                
                # Encontra a pista após a regra de voo
                string_after_rule = line[rule_index + 2:]
                pista_match = re.search(r'(\d{2})', string_after_rule)
                record['pista'] = pista_match.group(1) if pista_match else ''
                
                # Encontra o horário (último bloco de 4 dígitos antes da regra)
                string_before_rule = line[:rule_index]
                time_matches = re.findall(r'\d{4}', string_before_rule)
                if time_matches:
                    horario_str = time_matches[-1]
                    time_index = string_before_rule.rfind(horario_str)
                    record['destino'] = line[27:time_index].strip() or 'N/A'
                    
                    # Tenta processar a data/hora. Se falhar, timestamp continua None.
                    try:
                        data_str = line[9:15]
                        full_datetime_str = f"{data_str}{horario_str}"
                        dt_obj = datetime.strptime(full_datetime_str, '%d%m%y%H%M')
                        record['timestamp'] = dt_obj.isoformat() + 'Z'
                    except (ValueError, IndexError):
                        # Data inválida, mas o resto dos dados é mantido
                        pass
            
            records.append(record)

        except Exception as e:
            # Captura qualquer outro erro inesperado no parsing da linha
            # e adiciona o registro com os dados parciais que conseguiu obter
            print(f"Erro inesperado ao processar a linha: '{line.strip()}'. Erro: {e}")
            records.append(record)
    
    return records

@app.route('/')
def index():
    """ Rota principal que renderiza a página HTML. """
    return render_template('index.html')

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """
    Rota da API para receber o arquivo, processá-lo com pandas
    e retornar os dados como JSON. Rota protegida por autenticação.
    """
    # --- VERIFICAÇÃO DE AUTENTICAÇÃO ---
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"error": "Token de autorização não encontrado"}), 401
        
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        
        # (Opcional) Log para saber quem fez o upload
        print(f"Upload autorizado para o usuário: {decoded_token['uid']}")

    except Exception as e:
        print(f"Erro de autenticação: {e}")
        return jsonify({"error": "Token inválido ou expirado"}), 401
    
    # --- LÓGICA DE UPLOAD (código original) ---
    if 'dataFile' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    
    file = request.files['dataFile']
    
    if file.filename == '':
        return jsonify({"error": "Nome de arquivo inválido"}), 400

    try:
        content = io.StringIO(file.stream.read().decode("utf-8", errors='ignore')).getvalue()
        records = parse_data_file(content)
        
        if not records:
            return jsonify({"error": "Nenhum registro válido encontrado no arquivo"}), 400

        df = pd.DataFrame(records)
        json_data = df.to_json(orient='records', date_format='iso')
        
        return json_data

    except Exception as e:
        return jsonify({"error": f"Erro ao processar o arquivo: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)