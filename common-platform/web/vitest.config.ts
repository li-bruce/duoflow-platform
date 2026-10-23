import {defineConfig, mergeConfig} from 'vitest/config';
import base from './vite.config';

export default mergeConfig(base,defineConfig({test:{server:{deps:{inline:[/@copilotkit[\/\\]/]}}}}));
