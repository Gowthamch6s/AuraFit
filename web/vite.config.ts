import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{'/health':'http://127.0.0.1:8000','/metrics':'http://127.0.0.1:8000','/predictions':'http://127.0.0.1:8000','/targets':'http://127.0.0.1:8000','/agent':'http://127.0.0.1:8000','/data':'http://127.0.0.1:8000'}},test:{environment:'node'}});
