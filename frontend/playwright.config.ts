import {defineConfig} from '@playwright/test'
export default defineConfig({testDir:'./tests',timeout:45000,workers:1,use:{baseURL:'http://127.0.0.1:5175',channel:'chrome',headless:true,viewport:{width:1440,height:1000},launchOptions:{args:['--enable-unsafe-swiftshader']}},reporter:'list'})
